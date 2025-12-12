import boto3
import re
import time
from botocore.exceptions import ClientError


def _normalize_name(name: str) -> str:
    """
    Ujednolicona normalizacja zgodna z GroupManager.
    Pozwala na znaki: a-z, A-Z, 0-9, +, =, ,, ., @, _, -
    NIE zamienia '_' na '-'!
    """
    return re.sub(r'[^a-zA-Z0-9+=,.@_-]', '', name)


class UserManager:
    def __init__(self):
        self.iam_client = boto3.client('iam')

    def create_users_for_group(self, users: list[str], group_name: str) -> str:
        created_users = []

        # Używamy nowej normalizacji (nie zamieni _ na -)
        group_name = _normalize_name(group_name)

        for user in users:
            # Tworzymy unikalną nazwę usera: User-Grupa
            raw_username = f"{user}-{group_name}"
            username = _normalize_name(raw_username)

            try:
                # 1. Tworzenie użytkownika
                self.iam_client.create_user(
                    UserName=username,
                    Tags=[{'Key': 'Group', 'Value': group_name}]
                )
                created_users.append(username)
                print(f"   👤 Utworzono użytkownika '{username}'")

                # 2. Tworzenie profilu logowania (hasło)
                self.iam_client.create_login_profile(
                    UserName=username,
                    Password=group_name,
                    PasswordResetRequired=True
                )
                print(f"      🔑 Ustawiono hasło dla '{username}'")

                # 3. Dodawanie do grupy
                self.iam_client.add_user_to_group(
                    GroupName=group_name,
                    UserName=username
                )
                print(f"      tg Dodano '{username}' do grupy '{group_name}'")

            except ClientError as e:
                error_code = e.response['Error']['Code']

                if error_code == 'EntityAlreadyExists':
                    print(f"⚠️ Użytkownik '{username}' już istnieje, pomijam.")
                    continue

                # Specjalna obsługa braku grupy - uruchamia Rollback
                elif error_code == 'NoSuchEntity' and 'group' in e.response['Error']['Message'].lower():
                    print(f"❌ KRYTYCZNY BŁĄD: Grupa '{group_name}' nie istnieje w AWS!")
                    self._rollback_users(created_users)
                    return f"Operacja przerwana: Grupa '{group_name}' nie istnieje."

                # Inne błędy
                print(f"❌ Błąd przy użytkowniku '{username}': {e}")
                self._rollback_users(created_users)
                return f"Operacja przerwana: Błąd przy '{username}' - {e}"

        return f"Pomyślnie przetworzono {len(users)} użytkowników dla grupy '{group_name}'."

    def _rollback_users(self, created_users):
        """Pomocnicza metoda do cofania zmian (sprzątania) w razie błędu."""
        print("🔄 Rozpoczynam wycofywanie zmian (Rollback)...")
        for user in created_users:
            try:
                # Najpierw musimy usunąć profil logowania
                try:
                    self.iam_client.delete_login_profile(UserName=user)
                except ClientError as e:
                    if e.response['Error']['Code'] != 'NoSuchEntity':
                        print(f"   Błąd usuwania profilu dla {user}: {e}")

                # Na koniec usuwamy usera
                self.iam_client.delete_user(UserName=user)
                print(f"   🗑️ Usunięto użytkownika '{user}'")
            except ClientError as rollback_error:
                print(f"   ❌ Nie udało się cofnąć zmian dla '{user}': {rollback_error}")