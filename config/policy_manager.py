import os
import re
import logging
import json
from pathlib import Path
from typing import List, Set
from botocore.exceptions import ClientError
import boto3


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


class PolicyManager:
    def __init__(self, policies_dir: str = "config/policies"):
        # Ustawiamy ścieżkę relatywnie do miejsca uruchomienia aplikacji (root projektu)
        self.policies_path = Path(os.getcwd()) / policies_dir
        self.iam_client = boto3.client('iam')

    def get_available_services(self) -> List[str]:
        """
        Zwraca listę usług, które posiadają parę plików polityk:
        leader_{usluga}_policy.json AND student_{usluga}_policy.json
        """
        if not self.policies_path.exists():
            logging.warning(f"⚠️ Katalog polityk nie istnieje: {self.policies_path}")
            return []

        leader_services: Set[str] = set()
        student_services: Set[str] = set()

        # Regex: dopasowuje np. 'leader_ec2_policy.json' i wyciąga 'ec2'
        leader_pattern = re.compile(r"^leader_(.*?)_policy\.json$")
        student_pattern = re.compile(r"^student_(.*?)_policy\.json$")

        try:
            for entry in self.policies_path.iterdir():
                if entry.is_file():
                    filename = entry.name

                    # Sprawdź czy to plik lidera
                    l_match = leader_pattern.match(filename)
                    if l_match:
                        leader_services.add(l_match.group(1))
                        continue

                    # Sprawdź czy to plik studenta
                    s_match = student_pattern.match(filename)
                    if s_match:
                        student_services.add(s_match.group(1))
                        continue

            # Część wspólna (intersekcja) obu zbiorów
            available = list(leader_services.intersection(student_services))
            available.sort()

            logging.info(f"✅ Znalezione dostępne usługi (policy match): {available}")
            return available

        except Exception as e:
            logging.error(f"❌ Błąd podczas skanowania polityk: {e}")
            return []
    
    def assign_policies_to_target(self, resource_types, group_name=None, user_name=None):
        """
        Automatycznie dobiera i przypisuje polityki:
        - Jeśli podano group_name -> Szuka plików 'student_{resource}_policy.json'
        - Jeśli podano user_name  -> Szuka plików 'leader_{resource}_policy.json'
        """

        # 1. Określenie roli i celu na podstawie argumentów
        if group_name and user_name:
            raise ValueError("❌ Błąd: Podaj albo group_name, albo user_name - nie oba naraz.")

        if group_name:
            prefix = "student"  # Grupa = Studenci
            target_name = _normalize_name(group_name)
            target_type = "Group"
        elif user_name:
            prefix = "leader"  # Użytkownik = Leader/Prowadzący
            target_name = _normalize_name(user_name)
            target_type = "User"
        else:
            raise ValueError("❌ Błąd: Musisz podać group_name lub user_name.")

        logging.info(f"🔄 Rozpoczynam przypisywanie polityk typu '{prefix.upper()}' dla: {target_name} ({target_type})")

        for resource in resource_types:
            # 2. Budowanie nazwy pliku (np. leader_s3_policy.json lub student_s3_policy.json)
            policy_filename = f"{prefix}_{resource}_policy.json"
            policy_path = os.path.join('config', 'policies', policy_filename)

            # 3. Sprawdzenie czy plik istnieje
            if not os.path.isfile(policy_path):
                # W tej implementacji brak pliku traktujemy jako błąd (spójnie z dotychczasowym zachowaniem endpointu)
                raise FileNotFoundError(f"Plik polityki '{policy_path}' nie istnieje.")

            # 4. Wczytanie JSON
            try:
                with open(policy_path, 'r') as policy_file:
                    policy_document = json.load(policy_file)
            except json.JSONDecodeError as e:
                logging.error(f"❌ Błąd składni JSON w pliku '{policy_filename}': {e}")
                raise e

            # Nazwa polityki wewnątrz IAM (np. student_s3_policy)
            policy_name_iam = f"{prefix}_{resource}_policy"
            policy_json_str = json.dumps(policy_document)

            try:
                # 5. Przypisanie w zależności od typu celu
                if target_type == "Group":
                    self.iam_client.put_group_policy(
                        GroupName=target_name,
                        PolicyName=policy_name_iam,
                        PolicyDocument=policy_json_str
                    )
                else:  # User
                    self.iam_client.put_user_policy(
                        UserName=target_name,
                        PolicyName=policy_name_iam,
                        PolicyDocument=policy_json_str
                    )

                logging.info(f"✅ Przypisano: {policy_filename} -> {target_name}")

            except ClientError as e:
                logging.error(f"❌ Błąd AWS przy przypisywaniu '{policy_name_iam}': {e}")
                raise e