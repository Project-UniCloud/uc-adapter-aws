import boto3
import logging
from botocore.exceptions import ClientError
from common.naming import normalize_name

# Initialize logger for this module
logger = logging.getLogger(__name__)


class UserManager:
    def __init__(self):
        self.iam_client = boto3.client('iam')

    def create_users_for_group(self, users: list[str], group_name: str) -> str:
        """
        Creates IAM users, sets passwords, and adds them to the specified group.
        Handles normalization of names automatically.
        """
        created_users = []

        # Use centralized normalization
        group_name = normalize_name(group_name)

        logger.info(f"Creating {len(users)} users for group: {group_name}")

        for user in users:
            # Create unique username: User-Group
            raw_username = f"{user}-{group_name}"
            username = normalize_name(raw_username)

            try:
                # 1. Create User
                self.iam_client.create_user(
                    UserName=username,
                    Tags=[{'Key': 'Group', 'Value': group_name}]
                )
                created_users.append(username)
                logger.info(f"   👤 Created user '{username}'")

                # 2. Create Login Profile (Password = group_name)
                self.iam_client.create_login_profile(
                    UserName=username,
                    Password=f'{username}_password123$',
                    PasswordResetRequired=True
                )
                logger.info(f"      🔑 Password set for '{username}'")

                # 3. Add to Group
                self.iam_client.add_user_to_group(
                    GroupName=group_name,
                    UserName=username
                )
                logger.info(f"      tg Added '{username}' to group '{group_name}'")

            except ClientError as e:
                error_code = e.response['Error']['Code']

                if error_code == 'EntityAlreadyExists':
                    logger.warning(f"⚠️ User '{username}' already exists. Skipping.")
                    continue

                # Critical Error: The group does not exist
                elif error_code == 'NoSuchEntity' and 'group' in e.response['Error']['Message'].lower():
                    logger.error(f"❌ CRITICAL: Group '{group_name}' does not exist in AWS!")
                    self._rollback_users(created_users)
                    return f"Operation aborted: Group '{group_name}' does not exist."

                # Other errors triggers rollback
                logger.error(f"❌ Error creating user '{username}': {e}")
                self._rollback_users(created_users)
                return f"Operation aborted: Error with '{username}' - {e}"

        return f"Successfully processed {len(users)} users for group '{group_name}'."

    def _rollback_users(self, created_users):
        """
        Rollback mechanism: Cleans up users created during a failed operation.
        """
        logger.info("🔄 Starting Rollback process...")
        for user in created_users:
            try:
                # 1. Delete Login Profile first (required before deleting user)
                try:
                    self.iam_client.delete_login_profile(UserName=user)
                except ClientError as e:
                    if e.response['Error']['Code'] != 'NoSuchEntity':
                        logger.warning(f"   Error deleting login profile for {user}: {e}")

                # 2. Delete User
                self.iam_client.delete_user(UserName=user)
                logger.info(f"   🗑️ Rollback: Deleted user '{user}'")
            except ClientError as rollback_error:
                logger.error(f"   ❌ Rollback failed for '{user}': {rollback_error}")

    def delete_user(self, username: str) -> bool:
        """
        Removes an IAM user (student or leader) and all associated credentials/permissions.
        """
        logger.info(f"🗑️ Deleting IAM User identity: {username}")

        try:
            # 1. Delete Login Profile (Console Password)
            try:
                self.iam_client.delete_login_profile(UserName=username)
                logger.debug(f"   - Password deleted for {username}")
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchEntity':
                    raise e

            # 2. Delete Access Keys (API Credentials)
            paginator = self.iam_client.get_paginator('list_access_keys')
            for page in paginator.paginate(UserName=username):
                for key in page['AccessKeyMetadata']:
                    self.iam_client.delete_access_key(UserName=username, AccessKeyId=key['AccessKeyId'])
                    logger.debug(f"   - Access Key {key['AccessKeyId']} deleted")

            # 3. Remove user from ALL groups
            paginator = self.iam_client.get_paginator('list_groups_for_user')
            for page in paginator.paginate(UserName=username):
                for group in page['Groups']:
                    group_name = group['GroupName']
                    # 👇 POPRAWKA: self.iam -> self.iam_client
                    self.iam_client.remove_user_from_group(GroupName=group_name, UserName=username)
                    logger.debug(f"   - Removed from group {group_name}")

            # 4. Detach Managed Policies
            paginator = self.iam_client.get_paginator('list_attached_user_policies')
            for page in paginator.paginate(UserName=username):
                for policy in page['AttachedPolicies']:
                    self.iam_client.detach_user_policy(UserName=username, PolicyArn=policy['PolicyArn'])

            # 5. Delete Inline Policies
            paginator = self.iam_client.get_paginator('list_user_policies')
            for page in paginator.paginate(UserName=username):
                for policy_name in page['PolicyNames']:
                    self.iam_client.delete_user_policy(UserName=username, PolicyName=policy_name)

            # 6. Remove MFA devices
            mfa_paginator = self.iam_client.get_paginator('list_mfa_devices')
            for page in mfa_paginator.paginate(UserName=username):
                for mfa in page['MFADevices']:
                    self.iam_client.deactivate_mfa_device(UserName=username, SerialNumber=mfa['SerialNumber'])
                    self.iam_client.delete_virtual_mfa_device(SerialNumber=mfa['SerialNumber'])

            # 7. FINALLY: Delete the user entity itself
            self.iam_client.delete_user(UserName=username)
            logger.info(f"✅ IAM User {username} deleted successfully.")
            return True

        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchEntity':
                logger.warning(f"⚠️ User {username} does not exist.")
                return False
            logger.error(f"❌ Failed to delete user {username}: {e}")
            raise e

    def add_leader_to_existing_group(self, group_name: str, leader_name: str):
        """
        Adds a new leader to an ALREADY EXISTING group environment.
        """
        # 1. Reconstruct group names based on conventions
        group_name = normalize_name(group_name)
        leaders_group_name = f"Leaders-{group_name}"

        logger.info(f"➕ Adding new leader '{leader_name}' to existing group '{group_name}'")

        # 2. Prepare leader username
        raw_leader = f"{leader_name}-{group_name}"
        leader_user = normalize_name(raw_leader)

        # 3. Create User & Login Profile (Logic copied from creation flow)
        try:
            self.iam_client.create_user(
                UserName=leader_user,
                Tags=[{'Key': 'Group', 'Value': group_name}]
            )
            # Set initial password same as group name (or any other logic you prefer)
            self.iam_client.create_login_profile(
                UserName=leader_user, Password=f'{leader_user}_password123$', PasswordResetRequired=True
            )
            logger.info(f"   👤 Created new leader user '{leader_user}'")

        except ClientError as e:
            if e.response['Error']['Code'] == 'EntityAlreadyExists':
                logger.info(f"   ℹ️ Leader '{leader_user}' already exists. Updating tags/groups.")
                try:
                    self.iam_client.tag_user(
                        UserName=leader_user,
                        Tags=[{'Key': 'Group', 'Value': group_name}]
                    )
                except ClientError:
                    pass
            else:
                logger.error(f"   ❌ Error creating leader {leader_user}: {e}")
                return  # Stop if critical error occurs

        # 4. Add to IAM Groups
        # Warning: This assumes the groups already exist.
        # If there is a risk they don't, wrap in try-except or check existence first.

        # Add to Leaders Group (Permissions)
        self._add_user_to_group_safe(leaders_group_name, leader_user)

        # Add to Student Group (Visibility)
        self._add_user_to_group_safe(group_name, leader_user)

        logger.info(f"   ✅ Successfully added {leader_user} to groups.")

    def _add_user_to_group_safe(self, group, user):
        """
        Helper to add user to group ignoring errors if already added.
        """
        try:
            self.iam_client.add_user_to_group(GroupName=group, UserName=user)
        except ClientError:
            pass