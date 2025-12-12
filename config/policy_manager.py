import os
import re
import logging
from pathlib import Path
from typing import List, Set

class PolicyManager:
    def __init__(self, policies_dir: str = "config/policies"):
        # Ustawiamy ścieżkę relatywnie do miejsca uruchomienia aplikacji (root projektu)
        self.policies_path = Path(os.getcwd()) / policies_dir

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