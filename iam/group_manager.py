import boto3
import json
import os
from botocore.exceptions import ClientError

def _normalize_name(name: str) -> str:
    char_map = {
        'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n',
        'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
        'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N',
        'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z',
        ' ': '-', '_': '-'
    }
    for char, replacement in char_map.items():
        name = name.replace(char, replacement)
    return name

class GroupManager:
    def __init__(self):
        self.iam_client = boto3.client('iam')

    def group_exists(self, group_name: str) -> bool:
        group_name = _normalize_name(group_name)
        try:
            self.iam_client.get_group(GroupName=group_name)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchEntity":
                return False
            raise

    def create_group_with_leaders(self, resource_type: str, leaders: list[str], group_name: str):
        group_name = _normalize_name(group_name)

        try:
            self.iam_client.create_group(GroupName=group_name)
            print(f"Grupa '{group_name}' została utworzona.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'EntityAlreadyExists':
                print(f"Grupa '{group_name}' już istnieje.")
            else:
                raise
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

        change_pw_policy_path = os.path.join('config', 'policies', 'change_password_policy.json')
        if not os.path.isfile(change_pw_policy_path):
            raise FileNotFoundError(f"Plik polityki '{change_pw_policy_path}' nie istnieje.")

        with open(change_pw_policy_path, 'r') as policy_file:
            change_pw_policy_document = json.load(policy_file)

        try:
            self.iam_client.put_group_policy(
                GroupName=group_name,
                PolicyName='change_password_policy',
                PolicyDocument=json.dumps(change_pw_policy_document)
            )
            print(f"Polityka 'change_password_policy' została przypisana do grupy '{group_name}'.")
        except ClientError as e:
            print(f"Błąd podczas przypisywania polityki zmiany hasła do grupy: {e}")
            raise

        for leader in leaders:
            try:
                raw_leader = f"{leader}-{group_name}"
                leader = _normalize_name(raw_leader)
                self.iam_client.create_user(
                    UserName=leader,
                    Tags=[{'Key': 'Group', 'Value': group_name}])
                self.iam_client.create_login_profile(
                    UserName=leader,
                    Password=group_name,
                    PasswordResetRequired=True
                )
                print(f"Login profile dla użytkownika '{leader}' został utworzony.")
            except ClientError as e:
                if e.response['Error']['Code'] == 'EntityAlreadyExists':
                    print(f"Login profile dla użytkownika '{leader}' już istnieje.")
                else:
                    print(f"Błąd podczas tworzenia login profile: {e}")
                    raise

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

            try:
                self.iam_client.add_user_to_group(
                    GroupName=group_name,
                    UserName=leader
                )
                print(f"Użytkownik '{leader}' został dodany do grupy '{group_name}'.")
            except ClientError as e:
                print(f"Błąd podczas dodawania użytkownika '{leader}' do grupy: {e}")
                raise

    def delete_group_and_users(self, group_name: str) -> tuple[list[str], str]:
        """
        Removes all users from the IAM group (including their login profiles,
        access keys, and inline/managed policies) and then deletes the group
        itself along with its inline/managed policies.

        Returns:
            (removed_users, message)
        """
        group_name = _normalize_name(group_name)
        removed_users: list[str] = []

        # 1) Ensure group exists and get users
        try:
            paginator = self.iam_client.get_paginator('get_group')
            users: list[dict] = []
            for page in paginator.paginate(GroupName=group_name):
                users.extend(page.get('Users', []))
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == 'NoSuchEntity':
                return removed_users, f"Group '{group_name}' does not exist"
            raise

        # 2) For each user: clean and delete
        for user in users:
            username = user['UserName']
            try:
                # Remove from group first to break dependency
                try:
                    self.iam_client.remove_user_from_group(GroupName=group_name, UserName=username)
                except ClientError:
                    # continue even if already removed
                    pass

                # Detach managed policies
                attached = self.iam_client.list_attached_user_policies(UserName=username)
                for ap in attached.get('AttachedPolicies', []):
                    self.iam_client.detach_user_policy(UserName=username, PolicyArn=ap['PolicyArn'])

                # Delete inline policies
                inline = self.iam_client.list_user_policies(UserName=username)
                for pn in inline.get('PolicyNames', []):
                    self.iam_client.delete_user_policy(UserName=username, PolicyName=pn)

                # Delete access keys
                access_keys = self.iam_client.list_access_keys(UserName=username)
                for ak in access_keys.get('AccessKeyMetadata', []):
                    self.iam_client.delete_access_key(UserName=username, AccessKeyId=ak['AccessKeyId'])

                # Delete login profile (if exists)
                try:
                    self.iam_client.delete_login_profile(UserName=username)
                except ClientError:
                    pass

                # Delete signing certificates
                try:
                    certs = self.iam_client.list_signing_certificates(UserName=username)
                    for cert in certs.get('Certificates', []):
                        self.iam_client.delete_signing_certificate(UserName=username, CertificateId=cert['CertificateId'])
                except ClientError:
                    pass

                # Deactivate and delete MFA devices (virtual only)
                try:
                    mfas = self.iam_client.list_mfa_devices(UserName=username)
                    for mfa in mfas.get('MFADevices', []):
                        # Cannot delete hardware devices via API; just deactivate if needed
                        try:
                            self.iam_client.deactivate_mfa_device(UserName=username, SerialNumber=mfa['SerialNumber'])
                        except ClientError:
                            pass
                except ClientError:
                    pass

                # Finally delete the user
                self.iam_client.delete_user(UserName=username)
                removed_users.append(username)
            except ClientError as e:
                # Continue with other users but include info in message
                removed_users.append(f"ERROR:{username}:{e.response.get('Error', {}).get('Message', str(e))}")

        # 3) Clean and delete the group
        try:
            # Detach managed policies from the group
            attached = self.iam_client.list_attached_group_policies(GroupName=group_name)
            for ap in attached.get('AttachedPolicies', []):
                self.iam_client.detach_group_policy(GroupName=group_name, PolicyArn=ap['PolicyArn'])

            # Delete inline policies from the group
            inline = self.iam_client.list_group_policies(GroupName=group_name)
            for pn in inline.get('PolicyNames', []):
                self.iam_client.delete_group_policy(GroupName=group_name, PolicyName=pn)

            # Delete the group
            self.iam_client.delete_group(GroupName=group_name)
            return removed_users, f"Group '{group_name}' and {len([u for u in removed_users if not u.startswith('ERROR:')])} users removed"
        except ClientError as e:
            return removed_users, f"Failed to delete group '{group_name}': {e}"
