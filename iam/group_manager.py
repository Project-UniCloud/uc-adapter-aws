import boto3
import json
import os
import re
import logging
from botocore.exceptions import ClientError


def _normalize_name(name):
    """Usuwa znaki specjalne z nazwy grupy/użytkownika."""
    return re.sub(r'[^a-zA-Z0-9+=,.@_-]', '', name)


class GroupManager:
    def __init__(self):
        self.iam_client = boto3.client('iam')

    def group_exists(self, group_name):
        group_name = _normalize_name(group_name)
        try:
            self.iam_client.get_group(GroupName=group_name)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchEntity':
                return False
            raise e

    def assign_policies_to_target(self, resource_types, group_name=None, user_name=None):
        """
        Główna metoda przypisywania polityk.
        ZMIANA LOGIKI:
        - Jeśli podano user_name -> IGNORUJEMY (zgodność z gRPC, ale nie używamy już inline user policies).
        - Jeśli podano group_name -> Przypisujemy polityki 'student_' do tej grupy ORAZ 'leader_' do grupy 'Leaders-{group_name}'.
        """

        # 1. Obsługa starego podejścia (User) - Ignorujemy
        if user_name:
            logging.info(f"ℹ️ Wywołano AssignPolicies dla użytkownika '{user_name}'. "
                         "Ignoruję, ponieważ teraz używamy wyłącznie grup Liderów (Leaders-Group).")
            return

        if not group_name:
            raise ValueError("Musisz podać group_name.")

        logging.info(f"🛡️ Rozpoczynam aktualizację polityk dla ekosystemu grupy: {group_name}")

        leaders_group_name = f"Leaders-{group_name}"

        logging.info(f"   👉 Wgrywanie polityk STUDENTÓW do grupy: {group_name}")

        # Dodajemy automatycznie 'region' do studentów, jeśli nie ma go na liście
        student_resources = list(resource_types)
        if 'region' not in student_resources:
            student_resources.append('region')

        self._apply_policies_from_files(
            target_group=group_name,
            resource_types=student_resources,
            policy_prefix="student"
        )

        if self.group_exists(leaders_group_name):
            logging.info(f"   👉 Wgrywanie polityk LIDERÓW do grupy: {leaders_group_name}")

            leader_resources = [r for r in resource_types if r != 'region']

            self._apply_policies_from_files(
                target_group=leaders_group_name,
                resource_types=leader_resources,
                policy_prefix="leader"
            )
        else:
            logging.warning(f"⚠️ Grupa liderów '{leaders_group_name}' nie istnieje. Pomijam wgrywanie polityk lidera.")

    def _apply_policies_from_files(self, target_group, resource_types, policy_prefix):
        """Metoda pomocnicza: iteruje po zasobach i wgrywa pliki JSON do wskazanej grupy."""
        for resource in resource_types:
            # Obsługa nazewnictwa plików
            if resource == "region":
                if policy_prefix == 'leader':
                    continue
                policy_filename = "regional_restriction_policy.json"
                policy_name_iam = "regional_restriction_policy"
            else:
                policy_filename = f"{policy_prefix}_{resource}_policy.json"
                policy_name_iam = f"{policy_prefix}_{resource}_policy"

            policy_path = os.path.join('config', 'policies', policy_filename)

            if not os.path.isfile(policy_path):
                logging.debug(f"Plik '{policy_filename}' nie istnieje. Pomijam.")
                continue

            try:
                with open(policy_path, 'r') as f:
                    policy_doc = json.load(f)

                minified_policy = json.dumps(policy_doc, separators=(',', ':'))

                self.iam_client.put_group_policy(
                    GroupName=target_group,
                    PolicyName=policy_name_iam,
                    PolicyDocument=minified_policy
                )
                logging.info(f"      ✅ [{policy_prefix.upper()}] {policy_name_iam} -> {target_group}")

            except ClientError as e:
                logging.error(f"      ❌ Błąd AWS przy {policy_name_iam} dla {target_group}: {e}")
                raise e

    def create_group_with_leaders(self, resource_types: list[str], leaders: list[str], group_name: str):
        """
        Tworzy grupę studentów i liderów, a następnie wywołuje assign_policies_to_target
        do obsługi uprawnień.
        """
        group_name = _normalize_name(group_name)
        leaders_group_name = f"Leaders-{group_name}"

        logging.info(f"🚀 Tworzenie środowiska dla grupy: {group_name}")

        # 1. Tworzenie Grup
        try:
            self.iam_client.create_group(GroupName=group_name)
            logging.info(f"   Grupa studentów '{group_name}' gotowa.")
        except ClientError as e:
            if e.response['Error']['Code'] != 'EntityAlreadyExists':
                raise

        try:
            self.iam_client.create_group(GroupName=leaders_group_name)
            logging.info(f"   Grupa liderów '{leaders_group_name}' gotowa.")
        except ClientError as e:
            if e.response['Error']['Code'] != 'EntityAlreadyExists':
                raise

        self.assign_policies_to_target(resource_types, group_name=group_name)

        self._attach_change_password_policy(group_name)

        for leader in leaders:
            raw_leader = f"{leader}-{group_name}"
            leader_user = _normalize_name(raw_leader)

            try:
                self.iam_client.create_user(
                    UserName=leader_user,
                    Tags=[{'Key': 'Group', 'Value': group_name}]
                )
                self.iam_client.create_login_profile(
                    UserName=leader_user, Password=group_name, PasswordResetRequired=True
                )
                logging.info(f"   👤 Lider '{leader_user}' utworzony.")
            except ClientError as e:
                if e.response['Error']['Code'] != 'EntityAlreadyExists':
                    logging.error(f"   Błąd tworzenia usera {leader_user}: {e}")

            try:
                self.iam_client.add_user_to_group(GroupName=leaders_group_name, UserName=leader_user)
            except ClientError:
                pass

            try:
                self.iam_client.add_user_to_group(GroupName=group_name, UserName=leader_user)
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
        Bezpiecznie usuwa grupę studentów ORAZ grupę liderów i wszystkich powiązanych userów.
        Rozdziela proces na: opróżnianie grup -> usuwanie grup -> usuwanie userów.
        """
        messages = []
        # Używamy set (zbiór), aby uniknąć duplikatów (gdy Lider jest w obu grupach)
        users_to_delete = set()

        group_name = _normalize_name(group_name)
        groups_to_clean = [group_name, f"Leaders-{group_name}"]

        # FAZA 1: Opróżnianie i usuwanie grup
        for g in groups_to_clean:
            logging.info(f"🧹 Przetwarzanie grupy: {g}")
            try:
                # 1. Pobierz userów i wypisz ich z grupy (Detach)
                try:
                    paginator = self.iam_client.get_paginator('get_group')
                    for page in paginator.paginate(GroupName=g):
                        for u in page['Users']:
                            u_name = u['UserName']
                            users_to_delete.add(u_name)  # Dodaj do listy "do usunięcia później"

                            try:
                                self.iam_client.remove_user_from_group(GroupName=g, UserName=u_name)
                            except ClientError as e:
                                if e.response['Error']['Code'] == 'NoSuchEntity':
                                    pass  # User już nie istnieje lub nie jest w grupie
                                else:
                                    logging.warning(f"Błąd wypisywania {u_name} z {g}: {e}")

                except ClientError as e:
                    if e.response['Error']['Code'] == 'NoSuchEntity':
                        logging.info(f"Grupa {g} już nie istnieje. Pomijam.")
                        continue  # Przejdź do następnej grupy
                    raise e

                # 2. Usuwanie polityk inline grupy
                try:
                    p_res = self.iam_client.list_group_policies(GroupName=g)
                    for p_name in p_res['PolicyNames']:
                        self.iam_client.delete_group_policy(GroupName=g, PolicyName=p_name)
                except Exception as e:
                    logging.warning(f"Błąd usuwania polityk grupy {g}: {e}")

                # 3. Usuwanie samej grupy
                self.iam_client.delete_group(GroupName=g)
                messages.append(f"Grupa {g} usunięta.")
                logging.info(f"✅ Usunięto grupę: {g}")

            except ClientError as e:
                msg = f"Błąd przy usuwaniu grupy {g}: {e}"
                logging.error(msg)
                messages.append(msg)

        # FAZA 2: Usuwanie użytkowników (unikalnych)
        # Teraz, gdy grupy nie istnieją (lub są puste), możemy bezpiecznie usunąć userów
        logging.info(f"💀 Rozpoczynam usuwanie {len(users_to_delete)} użytkowników...")

        removed_users_list = []

        for u_name in users_to_delete:
            try:
                # A. Usuń Profil Logowania (Hasło)
                try:
                    self.iam_client.delete_login_profile(UserName=u_name)
                except ClientError as e:
                    if e.response['Error']['Code'] != 'NoSuchEntity':
                        logging.warning(f"Błąd usuwania profilu {u_name}: {e}")

                # B. Usuń Access Keys (Kluczowe! Inaczej będzie DeleteConflict)
                try:
                    keys = self.iam_client.list_access_keys(UserName=u_name)
                    for key in keys['AccessKeyMetadata']:
                        self.iam_client.delete_access_key(UserName=u_name, AccessKeyId=key['AccessKeyId'])
                except ClientError as e:
                    logging.warning(f"Błąd usuwania kluczy {u_name}: {e}")

                # C. Usuń polityki inline użytkownika
                try:
                    p_list = self.iam_client.list_user_policies(UserName=u_name)
                    for p_name in p_list['PolicyNames']:
                        self.iam_client.delete_user_policy(UserName=u_name, PolicyName=p_name)
                except ClientError:
                    pass

                # D. Odepnij polityki zarządzane (Managed Policies) - rzadkie, ale możliwe
                try:
                    mp_list = self.iam_client.list_attached_user_policies(UserName=u_name)
                    for mp in mp_list['AttachedPolicies']:
                        self.iam_client.detach_user_policy(UserName=u_name, PolicyArn=mp['PolicyArn'])
                except ClientError:
                    pass

                # E. Usuń użytkownika
                self.iam_client.delete_user(UserName=u_name)
                removed_users_list.append(u_name)
                logging.info(f"   🗑️ Usunięto usera: {u_name}")

            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchEntity':
                    logging.info(f"User {u_name} już nie istnieje.")
                else:
                    logging.error(f"Nie udało się usunąć usera {u_name}: {e}")

        return removed_users_list, "; ".join(messages)