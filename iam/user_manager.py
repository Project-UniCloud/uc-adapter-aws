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
                    Password=group_name,
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

        IMPORTANT: This method ONLY deletes the IAM identity. It does NOT delete
        actual AWS resources (e.g., EC2 instances, S3 buckets) created by this user.
        Those resources should be cleaned up separately by the ResourceCleaner based on tags.

        This implementation iterates through all groups, ensuring Leaders (who may happen
        to be in multiple groups) are removed correctly.
        """
        logger.info(f"🗑️ Deleting IAM User identity: {username}")

        try:
            # 1. Delete Login Profile (Console Password)
            try:
                self.iam.delete_login_profile(UserName=username)
                logger.debug(f"   - Password deleted for {username}")
            except ClientError as e:
                # If the user has no console access (no password), we can proceed.
                if e.response['Error']['Code'] != 'NoSuchEntity':
                    raise e

            # 2. Delete Access Keys (API Credentials)
            paginator = self.iam.get_paginator('list_access_keys')
            for page in paginator.paginate(UserName=username):
                for key in page['AccessKeyMetadata']:
                    self.iam.delete_access_key(UserName=username, AccessKeyId=key['AccessKeyId'])
                    logger.debug(f"   - Access Key {key['AccessKeyId']} deleted")

            # 3. Remove user from ALL groups
            # (Essential for Leaders who might be members of both a 'Leaders' group and a 'Students' group)
            paginator = self.iam.get_paginator('list_groups_for_user')
            for page in paginator.paginate(UserName=username):
                for group in page['Groups']:
                    group_name = group['GroupName']
                    self.iam.remove_user_from_group(GroupName=group_name, UserName=username)
                    logger.debug(f"   - Removed from group {group_name}")

            # 4. Detach Managed Policies (Permissions attached directly to the user)
            paginator = self.iam.get_paginator('list_attached_user_policies')
            for page in paginator.paginate(UserName=username):
                for policy in page['AttachedPolicies']:
                    self.iam.detach_user_policy(UserName=username, PolicyArn=policy['PolicyArn'])

            # 5. Delete Inline Policies (Embedded permissions)
            paginator = self.iam.get_paginator('list_user_policies')
            for page in paginator.paginate(UserName=username):
                for policy_name in page['PolicyNames']:
                    self.iam.delete_user_policy(UserName=username, PolicyName=policy_name)

            # 6. Remove MFA devices (if any exist)
            mfa_paginator = self.iam.get_paginator('list_mfa_devices')
            for page in mfa_paginator.paginate(UserName=username):
                for mfa in page['MFADevices']:
                    self.iam.deactivate_mfa_device(UserName=username, SerialNumber=mfa['SerialNumber'])
                    self.iam.delete_virtual_mfa_device(SerialNumber=mfa['SerialNumber'])

            # 7. FINALLY: Delete the user entity itself
            self.iam.delete_user(UserName=username)
            logger.info(f"✅ IAM User {username} deleted successfully.")
            return True

        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchEntity':
                logger.warning(f"⚠️ User {username} does not exist.")
                # Returning False as the operation didn't happen, but it's not a critical error
                return False
            logger.error(f"❌ Failed to delete user {username}: {e}")
            raise e