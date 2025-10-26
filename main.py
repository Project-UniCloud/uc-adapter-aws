import grpc
import logging
from concurrent import futures
from dotenv import load_dotenv
from botocore.exceptions import ClientError

import adapter_interface_pb2_grpc as pb2_grpc
import adapter_interface_pb2 as pb2

from iam.group_manager import GroupManager
from iam.user_manager import UserManager
from cost_monitoring import limit_manager as limits_manager
from clean_resources.cloud_adapter_server import find_resources_by_group, delete_resource

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

load_dotenv()
group_manager = GroupManager()

class CloudAdapterServicer(pb2_grpc.CloudAdapterServicer):
    def __init__(self):
        self.user_manager = UserManager()

    def GetStatus(self, request, context):
        logging.info("🔍 Sprawdzanie statusu serwera")
        response = pb2.StatusResponse()
        response.isHealthy = True
        return response

    def GroupExists(self, request, context):
        logging.info(f"🔍 Sprawdzanie czy grupa istnieje: {request.groupName}")
        try:
            group_exists = group_manager.group_exists(group_name=request.groupName)
            response = pb2.GroupExistsResponse()
            response.exists = group_exists
            return response
        except Exception as e:
            logging.error(f"❌ Błąd w GroupExists: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd podczas sprawdzania istnienia grupy: {e}")
            return pb2.GroupExistsResponse()

    def CreateUsersForGroup(self, request, context):
        logging.info(f"👥 Tworzenie użytkowników dla grupy: {request.groupName}")
        try:
            if not request.users:
                msg = "Lista użytkowników jest pusta"
                logging.warning(f"⚠️ {msg}")
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(msg)
                return pb2.CreateUsersForGroupResponse()

            result_msg = self.user_manager.create_users_for_group(
                users=list(request.users),
                group_name=request.groupName
            )
            response = pb2.CreateUsersForGroupResponse()
            response.message = result_msg
            return response
        except ClientError as e:
            logging.error(f"❌ Błąd AWS (CreateUsersForGroup): {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd AWS: {e}")
            return pb2.CreateUsersForGroupResponse()
        except Exception as e:
            logging.error(f"❌ Błąd w CreateUsersForGroup: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd: {e}")
            return pb2.CreateUsersForGroupResponse()

    def CreateGroupWithLeaders(self, request, context):
        logging.info(f"🏗️ Tworzenie grupy '{request.groupName}' z liderami: {request.leaders}")
        try:
            if not request.leaders:
                msg = "Lista liderów jest pusta"
                logging.warning(f"⚠️ {msg}")
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(msg)
                return pb2.GroupCreatedResponse()

            group_manager.create_group_with_leaders(
                resource_type=request.resourceType,
                leaders=list(request.leaders),
                group_name=request.groupName
            )
            response = pb2.GroupCreatedResponse()
            response.groupName = request.groupName
            return response
        except FileNotFoundError as e:
            logging.error(f"❌ Plik nie znaleziony: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(e))
            return pb2.GroupCreatedResponse()
        except ClientError as e:
            logging.error(f"❌ Błąd AWS: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd AWS: {e}")
            return pb2.GroupCreatedResponse()
        except Exception as e:
            logging.error(f"❌ Nieoczekiwany błąd: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Nieoczekiwany błąd: {e}")
            return pb2.GroupCreatedResponse()

    def GetTotalCostForGroup(self, request, context):
        logging.info(f"💰 Pobieranie kosztów dla grupy: {request.groupName}, od: {request.startDate}")
        try:
            cost = limits_manager.get_total_cost_for_group(
                group_tag_value=request.groupName,
                start_date=request.startDate,
                end_date=request.endDate or None
            )
            response = pb2.CostResponse()
            response.amount = cost
            return response
        except Exception as e:
            logging.error(f"❌ Błąd w GetTotalCostForGroup: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd podczas pobierania kosztów grupy: {e}")
            return pb2.CostResponse()

    def GetGroupCostWithServiceBreakdown(self, request, context):
        logging.info(f"🔍 Pobieranie szczegółowych kosztów dla grupy: {request.groupName}, od: {request.startDate}")
        try:
            breakdown = limits_manager.get_group_cost_with_service_breakdown(
                group_tag_value=request.groupName,
                start_date=request.startDate,
                end_date=request.endDate or None
            )

            response = pb2.GroupServiceBreakdownResponse()
            response.total = breakdown['total']
            for service_name, amount in breakdown['by_service'].items():
                service_cost = response.breakdown.add()
                service_cost.serviceName = service_name
                service_cost.amount = amount

            return response
        except Exception as e:
            logging.error(f"❌ Błąd w GetGroupCostWithServiceBreakdown: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd podczas pobierania kosztów usług grupy: {e}")
            return pb2.GroupServiceBreakdownResponse()

    def GetTotalCostsForAllGroups(self, request, context):
        logging.info(f"📊 Pobieranie kosztów dla wszystkich grup od: {request.startDate}")
        try:
            costs_dict = limits_manager.get_total_costs_for_all_groups(
                start_date=request.startDate,
                end_date=request.endDate or None
            )
            response = pb2.AllGroupsCostResponse()
            for group, cost in costs_dict.items():
                group_cost = response.groupCosts.add()
                group_cost.groupName = group
                group_cost.amount = cost
            return response
        except Exception as e:
            logging.error(f"❌ Błąd w GetTotalCostsForAllGroups: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd podczas pobierania kosztów grup: {e}")
            return pb2.AllGroupsCostResponse()

    def GetTotalCost(self, request, context):
        logging.info(f"🌐 Pobieranie całkowitych kosztów AWS od: {request.startDate}")
        try:
            cost = limits_manager.get_total_aws_cost(
                start_date=request.startDate,
                end_date=request.endDate or None
            )
            response = pb2.CostResponse()
            response.amount = cost
            return response
        except Exception as e:
            logging.error(f"❌ Błąd w GetTotalAwsCost: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd podczas pobierania całkowitych kosztów AWS: {e}")
            return pb2.CostResponse()

    def GetTotalCostWithServiceBreakdown(self, request, context):
        logging.info(f"🧾 Pobieranie całkowitych kosztów AWS z podziałem na usługi od: {request.startDate}")
        try:
            result = limits_manager.get_total_cost_with_service_breakdown(
                start_date=request.startDate,
                end_date=request.endDate or None
            )
            response = pb2.GroupServiceBreakdownResponse()
            response.total = result['total']
            for service_name, amount in result['by_service'].items():
                entry = response.breakdown.add()
                entry.serviceName = service_name
                entry.amount = amount
            return response
        except Exception as e:
            logging.error(f"❌ Błąd w GetTotalCostWithServiceBreakdown: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd podczas pobierania kosztów AWS: {e}")
            return pb2.GroupServiceBreakdownResponse()

    def CleanupGroupResources(self, request, context):
        logging.info(f"Starting cleanup for group: {request.groupName}")
        group_name = request.groupName

        # 1️⃣ znajdź zasoby
        resources = find_resources_by_group("Group", group_name)

        if not resources:
            return pb2.CleanupGroupResponse(
                success=True,
                message=f"No resources found for group '{group_name}'"
            )

        # 2️⃣ usuń zasoby (jeśli deleteResources=True)
        deleted = []
        for r in resources:
            msg = delete_resource(r)
            deleted.append(msg)
            logging.info(msg)

        return pb2.CleanupGroupResponse(
            success=True,
            deletedResources=deleted,
            message=f"Cleanup completed for group '{group_name}'"
        )



def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_CloudAdapterServicer_to_server(CloudAdapterServicer(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    logging.info("🚀 Serwer działa na porcie 50051...")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()
