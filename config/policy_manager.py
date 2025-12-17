import os
import re
import logging
from pathlib import Path
from typing import List, Set

logger = logging.getLogger(__name__)

class PolicyManager:
    def __init__(self, policies_dir: str = "policies"):
        current_file_path = Path(__file__).resolve()
        self.policies_path = current_file_path.parent / "policies"

    def get_available_services(self) -> List[str]:
        """
        Scans the policies directory and returns a list of services that have
        both a 'leader_{service}_policy.json' and 'student_{service}_policy.json'.
        """
        if not self.policies_path.exists():
            logger.warning(f"⚠️ Policy directory not found: {self.policies_path}")
            return []

        leader_services: Set[str] = set()
        student_services: Set[str] = set()

        # Regex to match filenames like 'leader_ec2_policy.json'
        leader_pattern = re.compile(r"^leader_(.*?)_policy\.json$")
        student_pattern = re.compile(r"^student_(.*?)_policy\.json$")

        try:
            for entry in self.policies_path.iterdir():
                if entry.is_file():
                    filename = entry.name

                    # Check for leader policy
                    l_match = leader_pattern.match(filename)
                    if l_match:
                        leader_services.add(l_match.group(1))
                        continue

                    # Check for student policy
                    s_match = student_pattern.match(filename)
                    if s_match:
                        student_services.add(s_match.group(1))
                        continue

            # Intersection: Service is available only if BOTH policies exist
            available = list(leader_services.intersection(student_services))
            available.sort()

            logger.info(f"✅ Found available services (policy match): {available}")
            return available

        except Exception as e:
            logger.error(f"❌ Error scanning policies: {e}")
            return []