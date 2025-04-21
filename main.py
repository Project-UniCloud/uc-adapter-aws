import boto3
import os
from dotenv import load_dotenv

def main():
    load_dotenv()

    # Tworzenie sesji AWS
    session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION")  # niekonieczne dla IAM, ale zostaje
    )

    # Klient IAM
    iam = session.client('iam')

    # Nazwa nowego użytkownika
    new_user_name = "boto3-generated-user"

    try:
        response = iam.create_user(
            UserName=new_user_name
        )
        print(f"✅ Użytkownik '{new_user_name}' został utworzony.")
        print("Szczegóły:", response['User'])

    except iam.exceptions.EntityAlreadyExistsException:
        print(f"⚠️ Użytkownik '{new_user_name}' już istnieje.")
    except Exception as e:
        print(f"❌ Błąd podczas tworzenia użytkownika: {e}")

if __name__ == "__main__":
    main()
