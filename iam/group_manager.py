import boto3
import json
import os
from botocore.exceptions import ClientError

class GroupManager:
    def __init__(self):
        self.iam_client = boto3.client('iam')

    def create_group_with_leaders(self, resource_type: str, leaders: list[str]):
        group_name = f"{resource_type}_group"

        # 1. Tworzenie grupy
        try:
            self.iam_client.create_group(GroupName=group_name)
            print(f"Grupa '{group_name}' została utworzona.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'EntityAlreadyExists':
                print(f"Grupa '{group_name}' już istnieje.")
            else:
                raise

        # 2. Przypisywanie polityki do grupy
        policy_path = os.path.join('config', 'policies', f"student_{resource_type}_policy.json")
        if not os.path.isfile(policy_path):
            raise FileNotFoundError(f"Plik polityki '{policy_path}' nie istnieje.")

        with open(policy_path, 'r') as policy_file:
            policy_document = json.load(policy_file)

        try:
            self.iam_client.put_group_policy(
                GroupName=group_name,
                PolicyName=f"student_{resource_type}_policy",
                PolicyDocument=json.dumps(policy_document)
            )
            print(f"Polityka 'student_{resource_type}_policy' została przypisana do grupy '{group_name}'.")
        except ClientError as e:
            print(f"Błąd podczas przypisywania polityki do grupy: {e}")
            raise

        # 3. Tworzenie użytkowników prowadzących i przypisywanie ich do grupy
        for leader in leaders:
            try:
                self.iam_client.create_user(UserName=leader)
                print(f"Użytkownik '{leader}' został utworzony.")
            except ClientError as e:
                if e.response['Error']['Code'] == 'EntityAlreadyExists':
                    print(f"Użytkownik '{leader}' już istnieje.")
                else:
                    raise

            # Przypisywanie polityki do użytkownika
            leader_policy_path = os.path.join('config', 'policies', f'leader_{resource_type}_policy.json')
            if not os.path.isfile(leader_policy_path):
                raise FileNotFoundError(f"Plik polityki '{leader_policy_path}' nie istnieje.")

            with open(leader_policy_path, 'r') as leader_policy_file:
                leader_policy_document = json.load(leader_policy_file)

            try:
                self.iam_client.put_user_policy(
                    UserName=leader,
                    PolicyName=f'leader_{resource_type}_policy',
                    PolicyDocument=json.dumps(leader_policy_document)
                )
                print(f"Polityka 'leader_policy' została przypisana do użytkownika '{leader}'.")
            except ClientError as e:
                print(f"Błąd podczas przypisywania polityki do użytkownika '{leader}': {e}")
                raise

            # Dodawanie użytkownika do grupy
            try:
                self.iam_client.add_user_to_group(
                    GroupName=group_name,
                    UserName=leader
                )
                print(f"Użytkownik '{leader}' został dodany do grupy '{group_name}'.")
            except ClientError as e:
                print(f"Błąd podczas dodawania użytkownika '{leader}' do grupy: {e}")
                raise
