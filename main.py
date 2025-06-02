import grpc
import logging
from concurrent import futures
from dotenv import load_dotenv
from botocore.exceptions import ClientError

import adapter_interface_pb2_grpc as pb2_grpc
import adapter_interface_pb2 as pb2

from iam.group_manager import GroupManager
from iam.user_manager import UserManager

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
            group_exists = group_manager.group_exists(
                group_name=request.groupName
            )
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
            print(request.resourceType)
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


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_CloudAdapterServicer_to_server(CloudAdapterServicer(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    logging.info("🚀 Serwer działa na porcie 50051...")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()
