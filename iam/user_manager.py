import boto3
from botocore.exceptions import ClientError


def _normalize_username(name: str) -> str:
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


class UserManager:
    def __init__(self):
        self.iam_client = boto3.client('iam')

    def create_users_for_group(self, users: list[str], group_name: str) -> str:
        created_users = []
        group_name = _normalize_username(group_name)
        for user in users:
            raw_username = f"{user}-{group_name}"
            username = _normalize_username(raw_username)

            try:
                self.iam_client.create_user(
                    UserName=username,
                    Tags=[{'Key': 'Group', 'Value': group_name}]
                )
                created_users.append(username)
                print(f"Utworzono użytkownika '{username}' z tagiem 'Group': '{group_name}'")

                self.iam_client.create_login_profile(
                    UserName=username,
                    Password=group_name,
                    PasswordResetRequired=True
                )
                print(f"Ustawiono hasło '{group_name}' dla użytkownika '{username}' (wymagana zmiana przy logowaniu)")

                self.iam_client.add_user_to_group(
                    GroupName=group_name,
                    UserName=username
                )
                print(f"Dodano użytkownika '{username}' do grupy '{group_name}'")

            except ClientError as e:
                error_code = e.response['Error']['Code']

                if error_code == 'EntityAlreadyExists':
                    print(f"Użytkownik '{username}' już istnieje, pomijanie")
                    continue
                elif error_code == 'NoSuchEntity' and 'group' in e.response['Error']['Message'].lower():
                    print(f"Grupa '{group_name}' nie istnieje!")
                    for created_user in created_users:
                        try:
                            self.iam_client.delete_login_profile(UserName=created_user)
                        except ClientError:
                            pass
                        try:
                            self.iam_client.delete_user(UserName=created_user)
                            print(f"Usunięto użytkownika '{created_user}' podczas rollbacku")
                        except ClientError as rollback_error:
                            print(f"Błąd podczas rollbacku dla '{created_user}': {rollback_error}")
                    return f"Operacja przerwana: Grupa '{group_name}' nie istnieje"

                print(f"Błąd: {e} - Wycofywanie zmian dla {username}")
                for created_user in created_users:
                    try:
                        self.iam_client.delete_login_profile(UserName=created_user)
                    except ClientError:
                        pass
                    try:
                        self.iam_client.delete_user(UserName=created_user)
                        print(f"Usunięto użytkownika '{created_user}' podczas rollbacku")
                    except ClientError as rollback_error:
                        print(f"Błąd podczas rollbacku dla '{created_user}': {rollback_error}")

                return f"Operacja przerwana: Błąd przy tworzeniu użytkownika '{username}' - {e}"

        return f"Pomyślnie przetworzono {len(users)} użytkowników. Grupa: '{group_name}'"