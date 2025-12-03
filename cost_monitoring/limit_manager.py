import boto3
import logging
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# Create a Cost Explorer client
client = boto3.client('ce')


def get_total_cost_for_group(group_tag_value: str, start_date: str, end_date: str = None) -> float:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
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

        total = sum(
            float(result['Total']['UnblendedCost']['Amount'])
            for result in response['ResultsByTime']
        )
        return round(total, 2)

    except ClientError as error:
        logging.error(f"AWS error while fetching costs for group {group_tag_value}: {error}")
        return 0.0


def get_group_cost_with_service_breakdown(group_tag_value: str, start_date: str, end_date: str = None) -> dict:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start_date, 'End': end_date},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            Filter={
                'Tags': {
                    'Key': 'Group',
                    'Values': [group_tag_value]
                }
            },
            GroupBy=[
                {'Type': 'DIMENSION', 'Key': 'SERVICE'}
            ]
        )

        cost_by_service = {}
        total_cost = 0.0

        for result_by_time in response['ResultsByTime']:
            for group in result_by_time['Groups']:
                service_name = group['Keys'][0]
                amount = float(group['Metrics']['UnblendedCost']['Amount'])

                if amount <= 0:
                    continue

                cost_by_service[service_name] = cost_by_service.get(service_name, 0.0) + amount
                total_cost += amount

        return {
            'total': round(total_cost, 2),
            'by_service': {k: round(v, 2) for k, v in sorted(
                cost_by_service.items(),
                key=lambda item: item[1],
                reverse=True
            )}
        }

    except ClientError as error:
        print(f"AWS error while fetching service breakdown for group {group_tag_value}: {error}")
        return {
            'total': 0.0,
            'by_service': {}
        }


def _first_day_of_month(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _shift_months(dt: datetime, months: int) -> datetime:
    # Shift month preserving year, clamping to valid month range
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    day = min(dt.day, 28)  # day won't matter since we set to first later
    return dt.replace(year=year, month=month, day=day)


def _aws_service_to_short(name: str) -> str:
    n = (name or '').lower()
    # Explicit mappings by common substrings
    if 'ec2' in n or 'elastic compute cloud' in n:
        return 'ec2'
    if 'simple storage service' in n or n == 'amazon s3' or ' s3' in n:
        return 's3'
    if 'elastic block store' in n or 'ebs' in n:
        return 'ebs'
    if 'relational database service' in n or ' rds' in n:
        return 'rds'
    if 'cloudwatch' in n:
        return 'cloudwatch'
    if 'lambda' in n:
        return 'lambda'
    if 'elastic container registry' in n or ' ecr' in n:
        return 'ecr'
    if 'elastic kubernetes service' in n or ' eks' in n:
        return 'eks'
    if 'virtual private cloud' in n or ' vpc' in n:
        return 'vpc'
    if 'systems manager' in n or ' ssm' in n:
        return 'ssm'
    if 'key management service' in n or ' kms' in n:
        return 'kms'
    if 'dynamodb' in n:
        return 'dynamodb'
    if 'simple queue service' in n or ' sqs' in n:
        return 'sqs'
    if 'simple notification service' in n or ' sns' in n:
        return 'sns'
    if 'cloudtrail' in n:
        return 'cloudtrail'
    # Fallback: take last word/acronym
    for token in ['aurora', 'redshift', 'athena', 'glue', 'emr', 'opensearch', 'kinesis']:
        if token in n:
            return token
    # generic fallback: normalized first acronym-like
    return n.split(' - ')[0].split()[-1].replace('amazon', '').strip() or 'other'


def get_group_cost_last_6_months_by_service(group_tag_value: str) -> dict:
    """
    Zwraca mapę skrótów usług do łącznych kosztów z ostatnich 6 miesięcy (łącznie z bieżącym miesiącem),
    dla zasobów oznaczonych tagiem Group=<group_tag_value>.
    """
    now = datetime.now(timezone.utc)
    month_start = _first_day_of_month(now)
    start_dt = _first_day_of_month(_shift_months(month_start, -5))  # 6-miesięczne okno
    end_dt = _first_day_of_month(_shift_months(month_start, 1))     # CE End jest ekskluzywny

    start_date = start_dt.strftime('%Y-%m-%d')
    end_date = end_dt.strftime('%Y-%m-%d')

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start_date, 'End': end_date},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            Filter={
                'Tags': {
                    'Key': 'Group',
                    'Values': [group_tag_value]
                }
            },
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}]
        )

        costs: dict[str, float] = {}
        for by_time in response.get('ResultsByTime', []):
            for grp in by_time.get('Groups', []):
                aws_name = grp.get('Keys', [''])[0]
                amount = float(grp.get('Metrics', {}).get('UnblendedCost', {}).get('Amount', '0'))
                if amount <= 0:
                    continue
                short = _aws_service_to_short(aws_name)
                costs[short] = round(costs.get(short, 0.0) + amount, 10)  # accumulate with precision

        # round final values to 2 decimals
        return {k: round(v, 2) for k, v in costs.items()}

    except ClientError as error:
        logging.error(f"AWS error while fetching last 6 months costs for group {group_tag_value}: {error}")
        return {}

def get_total_costs_for_all_groups(start_date: str, end_date: str = None) -> dict:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    group_costs = {}

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start_date, 'End': end_date},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'TAG', 'Key': 'Group'}]
        )

        for result in response['ResultsByTime']:
            for group in result['Groups']:
                tag_key = group['Keys'][0]

                if '$' in tag_key:
                    group_name = tag_key.split('$', 1)[1]
                else:
                    group_name = tag_key

                cost = float(group['Metrics']['UnblendedCost']['Amount'])
                group_costs[group_name] = group_costs.get(group_name, 0.0) + cost

        return {group: round(cost, 2) for group, cost in group_costs.items()}

    except ClientError as error:
        print(f"AWS error while fetching group costs: {error}")
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
        print(f"AWS error while fetching total AWS cost: {error}")
        return 0.0


def get_total_cost_with_service_breakdown(start_date: str, end_date: str = None) -> dict:
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        response = client.get_cost_and_usage(
            TimePeriod={'Start': start_date, 'End': end_date},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}]
        )

        if not response.get('ResultsByTime'):
            print(f"Brak danych kosztów za okres: {start_date} - {end_date}")
            return {'total': 0.0, 'by_service': {}}

        total_cost = 0.0
        cost_by_service = {}

        for result_by_time in response['ResultsByTime']:
            total = result_by_time.get('Total', {})
            unblended = total.get('UnblendedCost', {})
            amount_str = unblended.get('Amount')
            if amount_str is not None:
                try:
                    total_cost += float(amount_str)
                except ValueError:
                    # Jeśli konwersja się nie powiedzie, ignorujemy ten wpis
                    pass

            for group in result_by_time.get('Groups', []):
                service_name = group.get('Keys', [None])[0]
                if not service_name:
                    continue

                metrics = group.get('Metrics', {})
                unblended_metric = metrics.get('UnblendedCost', {})
                amount_str = unblended_metric.get('Amount')

                if amount_str is None:
                    continue

                try:
                    amount = float(amount_str)
                except ValueError:
                    continue

                if amount <= 0:
                    continue

                cost_by_service[service_name] = cost_by_service.get(service_name, 0.0) + amount

        return {
            'total': round(total_cost, 2),
            'by_service': {k: round(v, 2) for k, v in sorted(
                cost_by_service.items(),
                key=lambda item: item[1],
                reverse=True
            )}
        }

    except ClientError as e:
        print(f"Błąd AWS: {e}")
        return {'total': 0.0, 'by_service': {}}
