import grpc
from concurrent import futures
import boto3
import os
from dotenv import load_dotenv

import adapter_interface_pb2_grpc, adapter_interface_pb2

load_dotenv()
session = boto3.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)

iam = session.client('iam')


class CloudAdapterServicer(adapter_interface_pb2_grpc.CloudAdapterServicer):
    def GetStatus(self, request, context):
        response = adapter_interface_pb2.StatusResponse()
        response.isHealthy = True
        return response

    def CreateUser(self, request, context):
        new_user_name = "boto3-generated-user"
        try:
            response = iam.create_user(UserName=new_user_name)
            print(f"✅ Użytkownik '{new_user_name}' został utworzony.")
            print("Szczegóły:", response['User'])

            user_response = adapter_interface_pb2.UserCreatedResponse()
            user_response.id = response['User']['UserName']
            return user_response

        except iam.exceptions.EntityAlreadyExistsException:
            print(f"⚠️ Użytkownik '{new_user_name}' już istnieje.")
            user_response = adapter_interface_pb2.UserCreatedResponse()
            user_response.id = new_user_name
            return user_response
        except Exception as e:
            print(f"❌ Błąd podczas tworzenia użytkownika: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Błąd podczas tworzenia użytkownika: {e}")
            return adapter_interface_pb2.UserCreatedResponse()


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    adapter_interface_pb2_grpc.add_CloudAdapterServicer_to_server(CloudAdapterServicer(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    print("Serwer działa na porcie 50051...")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()
