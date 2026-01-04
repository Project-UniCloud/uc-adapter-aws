import grpc
import logging
import sys
import time
from concurrent import futures
from dotenv import load_dotenv

# gRPC Imports (Ensure protobuf files are generated)
import adapter_interface_pb2_grpc as pb2_grpc
import adapter_interface_pb2 as pb2

# Logic Imports
from common.naming import normalize_name
from common.logger import setup_logger
from iam.group_manager import GroupManager
from iam.user_manager import UserManager
from cost.cost_manager import CostManager
from resources.resource_cleaner import find_resources_by_group, delete_resource
from config.policy_manager import PolicyManager

# Import System Health Check
from config.system_health import SystemHealthCheck

# 1. Setup Global Logging
setup_logger('root')
logger = logging.getLogger(__name__)

# 2. Load Environment Variables
load_dotenv()

# Global System Status Flag
AWS_ONLINE = False


def initialize_application():
    """
    Bootstrap function to prepare the environment.
    Runs diagnostics, configures AWS, and determines Online/Offline mode.
    """
    global AWS_ONLINE

    print("\n" + "=" * 60)
    print("      🚀 INITIALIZING AWS ADAPTER SYSTEM      ")
    print("=" * 60 + "\n")

    # Run Health Check & Auto-Remediation
    health_checker = SystemHealthCheck()

    # ensure_system_integrity checks connectivity, policies, and deploys infra if missing
    system_ready = health_checker.ensure_system_integrity()

    if system_ready:
        AWS_ONLINE = True
        logger.info("🟢 [STATUS] Mode: ONLINE. Full cloud integration active.")
    else:
        AWS_ONLINE = False
        logger.warning("🟠 [STATUS] Mode: OFFLINE. Running in local/restricted mode.")
        print("\n⚠️  WARNING: No AWS connection or critical local configuration missing.")
        print("   You can view logs and manage files, but cloud operations are disabled.\n")
        time.sleep(2)


class CloudAdapterServicer(pb2_grpc.CloudAdapterServicer):
    def __init__(self):
        """
        Initializes the gRPC Servicer and all backend managers.
        """
        try:
            # We initialize managers even in Offline mode,
            # though they might fail if they try to connect strictly in __init__.
            # Assuming managers handle lazy connection or we catch errors here.
            self.group_manager = GroupManager()
            self.user_manager = UserManager()
            self.cost_manager = CostManager()
            self.policy_manager = PolicyManager()

            mode = "ONLINE" if AWS_ONLINE else "OFFLINE"
            logger.info(f"🚀 CloudAdapterServicer initialized successfully (Mode: {mode}).")
        except Exception as e:
            logger.error(f"🔥 Critical Error initializing managers: {e}")
            # If initialization fails, we might want to exit or run in a broken state
            # depending on resilience requirements. Here we re-raise.
            raise e

    # ==========================================
    # HEALTH & CONFIG
    # ==========================================

    def GetStatus(self, request, context):
        logger.info("🔍 Health Check requested")
        # Return the actual global status determined at startup
        return pb2.StatusResponse(isHealthy=AWS_ONLINE)

    def GetAvailableServices(self, request, context):
        logger.info("🔍 Request: GetAvailableServices")
        try:
            services_list = self.policy_manager.get_available_services()
            response = pb2.GetAvailableServicesResponse()
            response.services.extend(services_list)
            return response
        except Exception as e:
            logger.error(f"❌ Error fetching services: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Internal error: {e}")
            return pb2.GetAvailableServicesResponse()

    # ==========================================
    # GROUP & USER MANAGEMENT
    # ==========================================

    def GroupExists(self, request, context):
        logger.info(f"🔍 Request: GroupExists (Name: {request.groupName})")
        if not AWS_ONLINE:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("System is OFFLINE")
            return pb2.GroupExistsResponse()

        try:
            exists = self.group_manager.group_exists(request.groupName)
            return pb2.GroupExistsResponse(exists=exists)
        except Exception as e:
            logger.error(f"❌ Error checking group: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb2.GroupExistsResponse()

    def CreateGroupWithLeaders(self, request, context):
        logger.info(f"🏗️ Request: CreateGroup '{request.groupName}' with resources: {request.resourceTypes}")
        if not AWS_ONLINE:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("System is OFFLINE")
            return pb2.GroupCreatedResponse()

        try:
            if not request.leaders or not request.resourceTypes:
                msg = "Leaders list and Resource Types list cannot be empty."
                logger.warning(f"⚠️ Validation failed: {msg}")
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(msg)
                return pb2.GroupCreatedResponse()

            self.group_manager.create_group_with_leaders(
                resource_types=list(request.resourceTypes),
                leaders=list(request.leaders),
                group_name=request.groupName
            )
            return pb2.GroupCreatedResponse(groupName=request.groupName)

        except Exception as e:
            logger.error(f"❌ Error creating group: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb2.GroupCreatedResponse()

    def CreateUsersForGroup(self, request, context):
        logger.info(f"👥 Request: CreateUsersForGroup (Group: {request.groupName})")
        if not AWS_ONLINE:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("System is OFFLINE")
            return pb2.CreateUsersForGroupResponse()

        try:
            if not request.users:
                msg = "User list is empty."
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(msg)
                return pb2.CreateUsersForGroupResponse()

            msg = self.user_manager.create_users_for_group(
                users=list(request.users),
                group_name=request.groupName
            )
            return pb2.CreateUsersForGroupResponse(message=msg)
        except Exception as e:
            logger.error(f"❌ Error creating users: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb2.CreateUsersForGroupResponse()

    def AssignPolicies(self, request, context):
        target = f"Group: {request.groupName}" if request.groupName else "Unknown"
        logger.info(f"🛡️ Request: AssignPolicies to {target}. Resources: {request.resourceTypes}")

        if not AWS_ONLINE:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            return pb2.AssignPoliciesResponse(success=False, message="Offline Mode")

        try:
            self.group_manager.assign_policies_to_target(
                resource_types=list(request.resourceTypes),
                group_name=request.groupName
            )
            return pb2.AssignPoliciesResponse(success=True, message="Policies assigned successfully.")
        except Exception as e:
            logger.error(f"❌ Error assigning policies: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb2.AssignPoliciesResponse(success=False, message=str(e))

    def RemoveGroup(self, request, context):
        logger.info(f"🗑️ Request: RemoveGroup (Name: {request.groupName})")

        if not AWS_ONLINE:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            return pb2.RemoveGroupResponse(success=False, message="Offline Mode")

        try:
            removed_users, msg = self.group_manager.delete_group_and_users(request.groupName)
            return pb2.RemoveGroupResponse(
                success=True,
                removedUsers=removed_users,
                message=msg
            )
        except Exception as e:
            logger.error(f"❌ Error removing group: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb2.RemoveGroupResponse(success=False, message=str(e))

    def DeleteUser(self, request, context):
        # 1. Extract raw data from the request
        raw_user = request.user_name
        raw_group = request.group_name

        # 2. Normalize the group name (same logic as used during creation)
        # E.g., "Lab Python" -> "labpython"
        normalized_group = normalize_name(raw_group)

        # 3. Construct the full AWS username: User-Group
        # This is the critical step to match the naming convention used during user creation.
        raw_full_username = f"{raw_user}-{normalized_group}"

        # 4. Final normalization of the full name
        # E.g., "Jan.Kowalski-labpython" -> "jan_kowalski-labpython"
        aws_username = normalize_name(raw_full_username)

        logger.info(f"👤 Request: Delete IAM User '{aws_username}' (Base: '{raw_user}', Group: '{normalized_group}')")

        if not AWS_ONLINE:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            return pb2.DeleteUserResponse(success=False, message="Offline Mode")

        try:
            # 5. Call the user manager with the fully constructed AWS username
            success = self.user_manager.delete_user(aws_username)

            if success:
                msg = f"User {aws_username} deleted successfully."
                return pb2.DeleteUserResponse(success=True, message=msg)
            else:
                return pb2.DeleteUserResponse(success=False, message=f"User {aws_username} not found.")

        except Exception as e:
            logger.error(f"❌ Error deleting user {aws_username}: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb2.DeleteUserResponse(success=False, message=str(e))

    # ==========================================
    # RESOURCE CLEANUP
    # ==========================================

    def CleanupGroupResources(self, request, context):
        # Normalize strictly for logging and consistency
        raw_name = request.groupName
        norm_name = normalize_name(raw_name)

        logger.info(f"🧹 Request: CleanupGroupResources for '{raw_name}' (Tag: '{norm_name}')")

        if not AWS_ONLINE:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            return pb2.CleanupGroupResponse(success=False, message="Offline Mode")

        try:
            resources = find_resources_by_group("Group", norm_name)
            if not resources:
                logger.info("   No resources found.")
                return pb2.CleanupGroupResponse(
                    success=True, message=f"No resources found for tag Group={norm_name}"
                )

            deleted_msgs = []
            for r in resources:
                msg = delete_resource(r)
                deleted_msgs.append(msg)

            return pb2.CleanupGroupResponse(
                success=True,
                deletedResources=deleted_msgs,
                message="Cleanup completed."
            )

        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}", exc_info=True)
            return pb2.CleanupGroupResponse(success=False, message=str(e))

    def GetResourceCount(self, request, context):
        norm_name = normalize_name(request.groupName)
        res_type = (request.resourceType or "").strip().lower()
        logger.info(f"📦 Request: GetResourceCount for '{norm_name}', type='{res_type}'")

        if not AWS_ONLINE:
            return pb2.ResourceCountResponse(count=0)

        try:
            resources = find_resources_by_group("Group", norm_name)
            count = sum(1 for r in resources if r.get("service") == res_type)
            return pb2.ResourceCountResponse(count=count)
        except Exception as e:
            logger.error(f"❌ Error counting resources: {e}")
            return pb2.ResourceCountResponse(count=0)

    # ==========================================
    # COST MONITORING
    # ==========================================

    def GetTotalCostForGroup(self, request, context):
        group_tag = normalize_name(request.groupName)
        logger.info(f"💰 Request: Cost for Group '{group_tag}'")

        if not AWS_ONLINE: return pb2.CostResponse(amount=0.0)

        try:
            cost = self.cost_manager.get_total_cost_for_group(
                group_tag_value=group_tag,
                start_date=request.startDate,
                end_date=request.endDate or None
            )
            return pb2.CostResponse(amount=cost)
        except Exception as e:
            logger.error(f"❌ Cost Error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb2.CostResponse()

    def GetGroupCostWithServiceBreakdown(self, request, context):
        group_tag = normalize_name(request.groupName)
        logger.info(f"💰 Request: Cost Breakdown for Group '{group_tag}'")

        if not AWS_ONLINE: return pb2.GroupServiceBreakdownResponse()

        try:
            data = self.cost_manager.get_group_cost_with_service_breakdown(
                group_tag_value=group_tag,
                start_date=request.startDate,
                end_date=request.endDate or None
            )

            resp = pb2.GroupServiceBreakdownResponse(total=data['total'])
            for svc, amt in data['by_service'].items():
                resp.breakdown.add(serviceName=svc, amount=amt)
            return resp
        except Exception as e:
            logger.error(f"❌ Cost Breakdown Error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb2.GroupServiceBreakdownResponse()

    def GetTotalCostsForAllGroups(self, request, context):
        logger.info("📊 Request: Total Costs For All Groups")

        if not AWS_ONLINE: return pb2.AllGroupsCostResponse()

        try:
            data = self.cost_manager.get_total_costs_for_all_groups(
                start_date=request.startDate,
                end_date=request.endDate or None
            )
            resp = pb2.AllGroupsCostResponse()
            for grp, cost in data.items():
                resp.groupCosts.add(groupName=grp, amount=cost)
            return resp
        except Exception as e:
            logger.error(f"❌ All Groups Cost Error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb2.AllGroupsCostResponse()

    def GetTotalCost(self, request, context):
        logger.info("🌐 Request: Global AWS Cost")

        if not AWS_ONLINE: return pb2.CostResponse(amount=0.0)

        try:
            cost = self.cost_manager.get_total_aws_cost(
                start_date=request.startDate,
                end_date=request.endDate or None
            )
            return pb2.CostResponse(amount=cost)
        except Exception as e:
            logger.error(f"❌ Global Cost Error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb2.CostResponse()

    def GetTotalCostWithServiceBreakdown(self, request, context):
        logger.info("🧾 Request: Global AWS Cost Breakdown")

        if not AWS_ONLINE: return pb2.GroupServiceBreakdownResponse()

        try:
            data = self.cost_manager.get_total_cost_with_service_breakdown(
                start_date=request.startDate,
                end_date=request.endDate or None
            )
            resp = pb2.GroupServiceBreakdownResponse(total=data['total'])
            for svc, amt in data['by_service'].items():
                resp.breakdown.add(serviceName=svc, amount=amt)
            return resp
        except Exception as e:
            logger.error(f"❌ Global Breakdown Error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return pb2.GroupServiceBreakdownResponse()

    def GetGroupCostsLast6MonthsByService(self, request, context):
        group_tag = normalize_name(request.groupName)
        logger.info(f"🗓️ Request: 6-Month History for '{group_tag}'")

        if not AWS_ONLINE: return pb2.GroupCostMapResponse()

        try:
            data = self.cost_manager.get_group_cost_last_6_months_by_service(group_tag)
            resp = pb2.GroupCostMapResponse()
            for k, v in data.items():
                resp.costs[k] = v
            return resp
        except Exception as e:
            logger.error(f"❌ 6-Month Service Error: {e}")
            return pb2.GroupCostMapResponse()

    def GetGroupMonthlyCostsLast6Months(self, request, context):
        group_tag = normalize_name(request.groupName)
        logger.info(f"📅 Request: 6-Month Trend for '{group_tag}'")

        if not AWS_ONLINE: return pb2.GroupMonthlyCostsResponse()

        try:
            data = self.cost_manager.get_group_monthly_costs_last_6_months(group_tag)
            resp = pb2.GroupMonthlyCostsResponse()
            for k, v in data.items():
                resp.monthCosts[k] = v
            return resp
        except Exception as e:
            logger.error(f"❌ 6-Month Trend Error: {e}")
            return pb2.GroupMonthlyCostsResponse()


def serve():
    """
    Starts the gRPC server.
    """
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_CloudAdapterServicer_to_server(CloudAdapterServicer(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    logger.info("🚀 Server started on port 50051. Waiting for requests...")
    server.wait_for_termination()


if __name__ == '__main__':
    # 1. Bootstrap System (Health Check & Auto-Fix)
    initialize_application()

    # 2. Start gRPC Server
    serve()