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