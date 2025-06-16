import boto3
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# Klient do AWS Cost Explorer
client = boto3.client('ce')


def get_total_cost_for_group(group_tag_value: str, start_date: str, end_date: str = None) -> float:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        # Pobieranie danych z filtrowaniem po tagu
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start_date, 'End': end_date},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            Filter={
                'Tags': {
                    'Key': 'Group',
                    'Values': [group_tag_value]
                }
            }
        )

        # Sumowanie kosztów ze wszystkich okresów
        total = sum(
            float(result['Total']['UnblendedCost']['Amount'])
            for result in response['ResultsByTime']
        )
        return round(total, 2)

    except ClientError as error:
        print(f"Błąd AWS przy pobieraniu kosztów dla grupy {group_tag_value}: {error}")
        return 0.0


def get_total_costs_for_all_groups(start_date: str, end_date: str = None) -> dict:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    koszty_grup = {}

    try:
        # Paginacja dla dużych zestawów danych
        paginator = client.get_paginator('get_cost_and_usage')
        for page in paginator.paginate(
                TimePeriod={'Start': start_date, 'End': end_date},
                Granularity='MONTHLY',
                Metrics=['UnblendedCost'],
                GroupBy=[{'Type': 'TAG', 'Key': 'Group'}]
        ):
            # Przetwarzanie każdego wyniku
            for result in page['ResultsByTime']:
                for group in result['Groups']:
                    klucz_tagu = group['Keys'][0]

                    # Ekstrakcja nazwy grupy z formatu "Group$nazwa_grupy"
                    if '$' in klucz_tagu:
                        nazwa_grupy = klucz_tagu.split('$', 1)[1]
                    else:
                        nazwa_grupy = klucz_tagu

                    koszt = float(group['Metrics']['UnblendedCost']['Amount'])
                    koszty_grup[nazwa_grupy] = koszty_grup.get(nazwa_grupy, 0.0) + koszt

        return {grupa: round(koszt, 2) for grupa, koszt in koszty_grup.items()}

    except ClientError as error:
        print(f"Błąd AWS przy pobieraniu kosztów grup: {error}")
        return {}


def get_total_aws_cost(start_date: str, end_date: str = None) -> float:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start_date, 'End': end_date},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost']
        )

        total = sum(
            float(result['Total']['UnblendedCost']['Amount'])
            for result in response['ResultsByTime']
        )
        return round(total, 2)

    except ClientError as error:
        print(f"Błąd AWS przy pobieraniu całkowitego kosztu: {error}")
        return 0.0