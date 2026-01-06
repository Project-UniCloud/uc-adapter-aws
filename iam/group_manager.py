import boto3
import json
import os
import logging
from botocore.exceptions import ClientError
from common.naming import normalize_name

logger = logging.getLogger(__name__)


class GroupManager:
    def __init__(self):
        self.iam_client = boto3.client('iam')

    def group_exists(self, group_name: str) -> bool:
        """
        Checks if an IAM Group exists.
        Returns False if group_name is empty or does not exist.
        Raises ClientError for other AWS issues (e.g. Permissions).
        """
        group_name = normalize_name(group_name)

        # 1. Validation Guard
        if not group_name:
            logger.warning("⚠️ group_exists check called with empty name.")
            return False

        try:
            # 2. AWS Check
            self.iam_client.get_group(GroupName=group_name)
            return True

        except ClientError as e:
            # 3. Handle "Not Found" specifically
            if e.response['Error']['Code'] == 'NoSuchEntity':
                return False
            # 4. Re-raise other errors (Network, Permissions, etc.)
            logger.error(f"❌ AWS Error checking group '{group_name}': {e}")
            raise e

    def assign_policies_to_target(self, resource_types, group_name=None, user_name=None):
        """
        Assigns inline policies to a Group.
        Arguments user_name is ignored as we shifted to Group-only logic for Leaders.
        Supports additive updates (e.g., adding S3 to existing EC2 access).
        """
        if user_name:
            logger.info(f"ℹ️ AssignPolicies called for user '{user_name}'. Ignoring (using Leaders-Group strategy).")
            return

        if not group_name:
            raise ValueError("group_name is required.")

        # Normalizacja nazwy grupy studenckiej
        group_name = normalize_name(group_name)
        # Wyliczenie nazwy grupy prowadzących
        leaders_group_name = f"Leaders-{group_name}"

        logger.info(f"🛡️ Updating policies for group ecosystem: {group_name}")

        # --- 1. Aplikowanie polityk dla STUDENTÓW ---
        logger.info(f"   👉 Applying STUDENT policies to: {group_name}")

        # Kopiujemy listę, żeby nie modyfikować oryginału przy dodawaniu 'region'
        student_resources = list(resource_types)

        # Automatyczne dodanie blokady regionu dla studentów, jeśli jej nie ma
        # To kluczowe, bo studenci zawsze muszą mieć ograniczony region, niezależnie od serwisu
        if 'region' not in student_resources:
            student_resources.append('region')

        self._apply_policies_from_files(
            target_group=group_name,
            resource_types=student_resources,
            policy_prefix="student"
        )

        # --- 2. Aplikowanie polityk dla PROWADZĄCYCH (Leaders) ---
        if self.group_exists(leaders_group_name):
            logger.info(f"   👉 Applying LEADER policies to: {leaders_group_name}")

            # Prowadzący zazwyczaj nie potrzebują pliku 'region' (mają szersze uprawnienia lub inna politykę),
            # więc filtrujemy to, aby nie szukać nieistniejącego pliku 'leader_region_policy.json'
            leader_resources = [r for r in resource_types if r != 'region']

            self._apply_policies_from_files(
                target_group=leaders_group_name,
                resource_types=leader_resources,
                policy_prefix="leader"
            )
        else:
            logger.warning(f"⚠️ Leaders group '{leaders_group_name}' does not exist. Skipping leader policies.")

    def _apply_policies_from_files(self, target_group, resource_types, policy_prefix):
        """Helper: Reads JSON policies and puts them as Inline Group Policies."""
        target_group = normalize_name(target_group)

        for resource in resource_types:
            # Handle special naming for regional restriction
            if resource == "region":
                if policy_prefix == 'leader': continue
                policy_filename = "regional_restriction_policy.json"
                policy_name_iam = "regional_restriction_policy"
            else:
                policy_filename = f"{policy_prefix}_{resource}_policy.json"
                policy_name_iam = f"{policy_prefix}_{resource}_policy"

            # Assuming 'config/policies' is relative to the project root (where main.py runs)
            policy_path = os.path.join('config', 'policies', policy_filename)

            if not os.path.isfile(policy_path):
                logger.debug(f"Policy file '{policy_filename}' not found. Skipping.")
                continue

            try:
                with open(policy_path, 'r') as f:
                    policy_doc = json.load(f)

                # Minify JSON to save characters (AWS limit optimization)
                minified_policy = json.dumps(policy_doc, separators=(',', ':'))

                self.iam_client.put_group_policy(
                    GroupName=target_group,
                    PolicyName=policy_name_iam,
                    PolicyDocument=minified_policy
                )
                logger.info(f"      ✅ [{policy_prefix.upper()}] {policy_name_iam} -> {target_group}")

            except ClientError as e:
                logger.error(f"      ❌ AWS Error putting {policy_name_iam} on {target_group}: {e}")
                raise e

    def create_group_with_leaders(self, resource_types: list[str], leaders: list[str], group_name: str):
        """
        Orchestrates creation of Student Group + Leader Group + Leader Users + Policies.
        """
        group_name = normalize_name(group_name)
        leaders_group_name = f"Leaders-{group_name}"

        logger.info(f"🚀 Creating environment for group: {group_name}")

        # 1. Create IAM Groups
        self._create_iam_group_safe(group_name)
        self._create_iam_group_safe(leaders_group_name)

        # 2. Assign Policies (To both groups via internal logic)
        self.assign_policies_to_target(resource_types, group_name=group_name)

        # 3. Attach Password Policy (Force password change)
        self._attach_change_password_policy(group_name)
        self._attach_change_password_policy(leaders_group_name)

        # 4. Create Leaders and add them to BOTH groups
        for leader in leaders:
            raw_leader = f"{leader}-{group_name}"
            leader_user = normalize_name(raw_leader)

            try:
                self.iam_client.create_user(
                    UserName=leader_user,
                    Tags=[{'Key': 'Group', 'Value': group_name}]
                )
                self.iam_client.create_login_profile(
                    UserName=leader_user, Password=f'{leader_user}_password123$', PasswordResetRequired=True
                )
                logger.info(f"   👤 Created leader '{leader_user}'")
            except ClientError as e:
                if e.response['Error']['Code'] == 'EntityAlreadyExists':
                    try:
                        self.iam_client.tag_user(
                            UserName=leader_user,
                            Tags=[{'Key': 'Group', 'Value': group_name}]
                        )
                    except ClientError:
                        pass
                else:
                    logger.error(f"   Error creating leader {leader_user}: {e}")

            # Add leader to Technical Group (for extra permissions)
            self._add_user_to_group_safe(leaders_group_name, leader_user)
            # Add leader to Student Group (to see what students see)
            self._add_user_to_group_safe(group_name, leader_user)

    def _create_iam_group_safe(self, name):
        try:
            self.iam_client.create_group(GroupName=name)
            logger.info(f"   Group '{name}' created/verified.")
        except ClientError as e:
            if e.response['Error']['Code'] != 'EntityAlreadyExists':
                raise e

    def _add_user_to_group_safe(self, group, user):
        try:
            self.iam_client.add_user_to_group(GroupName=group, UserName=user)
        except ClientError:
            pass

    def _attach_change_password_policy(self, group_name):
        path = os.path.join('config', 'policies', 'change_password_policy.json')
        if os.path.isfile(path):
            with open(path, 'r') as f:
                doc = json.load(f)
            self.iam_client.put_group_policy(
                GroupName=group_name,
                PolicyName='change_password_policy',
                PolicyDocument=json.dumps(doc, separators=(',', ':'))
            )

    def delete_group_and_users(self, group_name):
        """
        Safely deletes the Student Group, Leader Group, and all associated users.
        """
        messages = []
        users_to_delete = set()

        group_name = normalize_name(group_name)
        groups_to_clean = [group_name, f"Leaders-{group_name}"]

        # --- Phase 1: Clean Groups ---
        for g in groups_to_clean:
            logger.info(f"🧹 Cleaning group: {g}")
            try:
                # 1. Get users and detach them
                paginator = self.iam_client.get_paginator('get_group')
                for page in paginator.paginate(GroupName=g):
                    for u in page['Users']:
                        users_to_delete.add(u['UserName'])
                        try:
                            self.iam_client.remove_user_from_group(GroupName=g, UserName=u['UserName'])
                        except ClientError:
                            pass

                # 2. Delete inline policies
                p_res = self.iam_client.list_group_policies(GroupName=g)
                for p_name in p_res['PolicyNames']:
                    self.iam_client.delete_group_policy(GroupName=g, PolicyName=p_name)

                # 3. Delete Group
                self.iam_client.delete_group(GroupName=g)
                msg = f"Group '{g}' deleted."
                messages.append(msg)
                logger.info(f"✅ {msg}")

            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchEntity':
                    logger.info(f"   ℹ️ Group '{g}' does not exist. Skipping.")
                else:
                    logger.error(f"   ❌ Error cleaning '{g}': {e}")
                    messages.append(f"Error: {e}")

        # --- Phase 2: Delete Users ---
        if not users_to_delete:
            final_msg = f"🏁 Cleanup finished for '{group_name}'. Groups processed, no users found."
            logger.info(final_msg)
            return [], "; ".join(messages)

        logger.info(f"💀 Deleting {len(users_to_delete)} unique users...")
        removed_users_list = []

        for u_name in users_to_delete:
            try:
                # A. Delete Login Profile
                try:
                    self.iam_client.delete_login_profile(UserName=u_name)
                except ClientError:
                    pass

                # B. Delete Access Keys
                try:
                    keys = self.iam_client.list_access_keys(UserName=u_name)
                    for key in keys['AccessKeyMetadata']:
                        self.iam_client.delete_access_key(UserName=u_name, AccessKeyId=key['AccessKeyId'])
                except ClientError:
                    pass

                # C. Delete Inline Policies
                try:
                    p_list = self.iam_client.list_user_policies(UserName=u_name)
                    for p_name in p_list['PolicyNames']:
                        self.iam_client.delete_user_policy(UserName=u_name, PolicyName=p_name)
                except ClientError:
                    pass

                # D. Detach Managed Policies
                try:
                    mp_list = self.iam_client.list_attached_user_policies(UserName=u_name)
                    for mp in mp_list['AttachedPolicies']:
                        self.iam_client.detach_user_policy(UserName=u_name, PolicyArn=mp['PolicyArn'])
                except ClientError:
                    pass

                # E. Delete User
                self.iam_client.delete_user(UserName=u_name)
                removed_users_list.append(u_name)
                logger.info(f"   🗑️ Deleted user: {u_name}")

            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchEntity':
                    logger.error(f"Failed to delete {u_name}: {e}")

        final_msg = f"🏁 Full cleanup complete for '{group_name}'. Removed {len(removed_users_list)} users and associated groups."
        logger.info(final_msg)

        return removed_users_list, "; ".join(messages)