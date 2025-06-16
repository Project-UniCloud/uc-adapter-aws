import boto3
import json
import logging
import re
from botocore.exceptions import ClientError

# Konfiguracja logowania
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def get_creator_name(user_identity):
    """Wyodrębnia nazwę użytkownika lub roli z userIdentity"""
    # Obsługa różnych typów tożsamości
    identity_type = user_identity.get('type', '')

    if identity_type == 'IAMUser':
        return user_identity.get('userName', 'unknown')

    elif identity_type == 'AssumedRole':
        # Wyodrębnij nazwę roli z ARN
        arn = user_identity.get('arn', '')
        if '/assumed-role/' in arn:
            parts = arn.split('/')
            if len(parts) >= 2:
                return parts[-2]  # Nazwa roli
            return parts[-1]
        elif ':role/' in arn:
            return arn.split('/')[-1]
        return 'assumed-role'

    elif identity_type == 'Root':
        return 'root'

    # Domyślny przypadek
    arn = user_identity.get('arn', '')
    if arn:
        return arn.split(':')[-1].split('/')[-1]

    return 'unknown'


def get_creator_group_tag(iam, user_identity):
    """Pobiera wartość tagu 'Group' z konta użytkownika/roli"""
    try:
        identity_name = get_creator_name(user_identity)
        identity_type = user_identity.get('type', '')

        logger.info(f"Getting group tag for {identity_name} ({identity_type})")

        if identity_type == 'IAMUser':
            logger.info(f"Listing tags for IAM user: {identity_name}")
            response = iam.list_user_tags(UserName=identity_name)
            tags = response.get('Tags', [])

        elif identity_type == 'AssumedRole':
            # Usuń ścieżkę z nazwy roli
            role_name = identity_name.split('/')[-1]
            logger.info(f"Listing tags for IAM role: {role_name}")
            response = iam.list_role_tags(RoleName=role_name)
            tags = response.get('Tags', [])

        elif identity_type == 'Root':
            # Dla roota nie ma tagów, zwracamy None
            logger.info("Root identity, no tags available")
            return 'None'
        else:
            logger.warning(f"Unhandled identity type: {identity_type}")
            tags = []

        logger.info(f"Found tags: {tags}")

        # Znajdź tag Group
        for tag in tags:
            if tag['Key'] == 'Group':
                return tag['Value']

        return 'None'

    except ClientError as e:
        logger.error(f"Error getting group tag: {e}")
        return 'None'


def lambda_handler(event, context):
    logger.info("Received event: " + json.dumps(event, indent=2))

    try:
        # Obsługa formatu EventBridge
        detail = event.get('detail', event)

        # Sprawdź czy to zdarzenie EC2 RunInstances
        if detail.get('eventSource') != 'ec2.amazonaws.com' or detail.get('eventName') != 'RunInstances':
            logger.info("Skipping non-EC2 event")
            return {'statusCode': 200, 'body': 'Skipped'}

        # Pobierz ID instancji
        items = detail['responseElements']['instancesSet']['items']
        if not items:
            logger.error("No instances found in event")
            return {'statusCode': 400, 'body': 'No instances found'}

        instance_id = items[0]['instanceId']
        logger.info(f"Processing instance: {instance_id}")

        # Pobierz informacje o twórcy
        user_identity = detail.get('userIdentity', {})

        # Inicjalizuj klientów AWS
        iam = boto3.client('iam')
        ec2 = boto3.client('ec2')

        # Uzyskaj nazwę twórcy
        creator_name = get_creator_name(user_identity)
        logger.info(f"Creator name: {creator_name}")

        # Uzyskaj wartość tagu Group
        group_value = get_creator_group_tag(iam, user_identity)
        logger.info(f"Group value: {group_value}")

        # Utwórz tagi
        tags = [
            {'Key': 'CreatedBy', 'Value': creator_name},
            {'Key': 'Group', 'Value': group_value},
            {'Key': 'AutoTagged', 'Value': 'true'}
        ]

        # Zastosuj tagi
        ec2.create_tags(Resources=[instance_id], Tags=tags)

        logger.info(f"Successfully tagged instance {instance_id} with tags: {tags}")
        return {
            'statusCode': 200,
            'body': json.dumps(f'Instance {instance_id} tagged successfully')
        }

    except Exception as e:
        logger.error(f"Error processing event: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error: {str(e)}')
        }