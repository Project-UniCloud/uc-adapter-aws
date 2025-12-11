import boto3
import json
import time
import zipfile
import io
import os
from botocore.exceptions import ClientError


class AutoTaggingDeployer:
    def __init__(self, region='us-east-1'):
        self.session = boto3.Session(region_name=region)
        self.iam = self.session.client('iam')
        self.lambda_client = self.session.client('lambda')
        self.events = self.session.client('events')
        self.sts = self.session.client('sts')

        # Stałe konfiguracyjne
        self.role_name = 'AutoTaggingLambdaRole'
        self.function_name = 'AutoTaggingFunction'
        self.rule_name = 'AutoTaggingMultiServiceRule'
        self.log_group_name = f'/aws/lambda/{self.function_name}'

    def _cleanup_existing_resources(self):
        """Sprząta stare wersje zasobów, aby wdrożenie było czyste."""
        print("🧹 Czyszczenie starych zasobów...")

        # 1. Usuń EventBridge Rule
        try:
            targets = self.events.list_targets_by_rule(Rule=self.rule_name)
            if targets.get('Targets'):
                self.events.remove_targets(
                    Rule=self.rule_name,
                    Ids=[target['Id'] for target in targets['Targets']]
                )
            self.events.delete_rule(Name=self.rule_name)
            print(f"   - Usunięto regułę EventBridge: {self.rule_name}")
        except (self.events.exceptions.ResourceNotFoundException, ClientError):
            pass

        # 2. Usuń funkcję Lambda
        try:
            self.lambda_client.delete_function(FunctionName=self.function_name)
            print(f"   - Usunięto funkcję Lambda: {self.function_name}")
        except (self.lambda_client.exceptions.ResourceNotFoundException, ClientError):
            pass

        # 3. Usuń Rolę IAM
        try:
            policies = self.iam.list_role_policies(RoleName=self.role_name)
            for policy_name in policies['PolicyNames']:
                self.iam.delete_role_policy(RoleName=self.role_name, PolicyName=policy_name)

            attached = self.iam.list_attached_role_policies(RoleName=self.role_name)
            for policy in attached['AttachedPolicies']:
                self.iam.detach_role_policy(RoleName=self.role_name, PolicyArn=policy['PolicyArn'])

            self.iam.delete_role(RoleName=self.role_name)
            print(f"   - Usunięto rolę IAM: {self.role_name}")
        except (self.iam.exceptions.NoSuchEntityException, ClientError):
            pass

        time.sleep(5)  # Krótka przerwa dla AWS API

    def _create_lambda_zip(self):
        """Pakuje kod funkcji do ZIP."""
        if not os.path.exists('lambda_function.py'):
            raise FileNotFoundError("❌ Nie znaleziono pliku lambda_function.py!")

        with open('lambda_function.py', 'r', encoding='utf-8') as f:
            lambda_code = f.read()

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zipped:
            zipped.writestr('lambda_function.py', lambda_code)
        buffer.seek(0)
        return buffer.read()

    def create_iam_role(self):
        """Tworzy rolę IAM z uprawnieniami do tagowania wielu usług."""
        print(f"🛡️ Tworzenie roli IAM: {self.role_name}...")

        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }

        role = self.iam.create_role(
            RoleName=self.role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description='Rola dla autotagowania studentow'
        )
        role_arn = role['Role']['Arn']

        # Czekamy na propagację roli
        time.sleep(10)

        # 1. Podstawowe logi
        self.iam.attach_role_policy(
            RoleName=self.role_name,
            PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
        )

        # 2. Custom Policy: Uprawnienia do tagowania WSZYSTKIEGO co obsługujemy
        policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "TaggingServices",
                    "Effect": "Allow",
                    "Action": [
                        "ec2:CreateTags",  # EC2, VPC, Subnets, etc.
                        "s3:PutBucketTagging",  # S3
                        "dynamodb:TagResource",  # DynamoDB
                        "lambda:TagResource",  # Lambda Functions
                        "rds:AddTagsToResource",  # RDS Databases
                        "elasticloadbalancing:AddTags"  # Load Balancers
                    ],
                    "Resource": "*"
                },
                {
                    "Sid": "ReadIAMTags",
                    "Effect": "Allow",
                    "Action": [
                        "iam:ListUserTags",
                        "iam:GetUser"
                    ],
                    "Resource": "*"
                }
            ]
        }

        self.iam.put_role_policy(
            RoleName=self.role_name,
            PolicyName='AutoTaggingPermissions',
            PolicyDocument=json.dumps(policy_document)
        )

        return role_arn

    def deploy_lambda_function(self, role_arn):
        """Wdraża kod na AWS Lambda."""
        print(f"⚡ Wdrażanie funkcji Lambda: {self.function_name}...")

        response = self.lambda_client.create_function(
            FunctionName=self.function_name,
            Runtime='python3.9',
            Role=role_arn,
            Handler='lambda_function.lambda_handler',
            Code={'ZipFile': self._create_lambda_zip()},
            Timeout=60,  # Wydłużony czas (standardowo 3s to za mało)
            MemorySize=256  # Troszkę więcej pamięci dla boto3
        )
        return response['FunctionArn']

    def setup_eventbridge_rule(self, lambda_arn):
        """Konfiguruje trigger (wyzwalacz) dla Lambdy."""
        print(f"📡 Konfiguracja EventBridge Rule: {self.rule_name}...")
        account_id = self.sts.get_caller_identity()['Account']

        # Definicja zdarzeń, które uruchomią autotagowanie
        event_pattern = {
            "source": [
                "aws.ec2",
                "aws.s3",
                "aws.dynamodb",
                "aws.lambda",
                "aws.rds",
                "aws.elasticloadbalancing"
            ],
            "detail-type": ["AWS API Call via CloudTrail"],
            "detail": {
                "eventSource": [
                    "ec2.amazonaws.com",
                    "s3.amazonaws.com",
                    "dynamodb.amazonaws.com",
                    "lambda.amazonaws.com",
                    "rds.amazonaws.com",
                    "elasticloadbalancing.amazonaws.com"
                ],
                "eventName": [
                    # --- EC2 & Sieć ---
                    "RunInstances",
                    "CreateVpc",
                    "CreateSubnet",
                    "CreateNatGateway",
                    "AllocateAddress",  # Elastic IP
                    "CreateVolume",  # Dyski EBS
                    "CreateInternetGateway",

                    # --- S3 ---
                    "CreateBucket",

                    # --- DynamoDB ---
                    "CreateTable",

                    # --- Lambda ---
                    "CreateFunction20150331",
                    "CreateFunction",

                    # --- RDS ---
                    "CreateDBInstance",

                    # --- Load Balancers ---
                    "CreateLoadBalancer"
                ]
            }
        }

        self.events.put_rule(
            Name=self.rule_name,
            EventPattern=json.dumps(event_pattern),
            State='ENABLED',
            Description='Automatyczne tagowanie EC2, S3, Dynamo, RDS, Lambda, Network'
        )

        self.events.put_targets(
            Rule=self.rule_name,
            Targets=[{'Id': '1', 'Arn': lambda_arn}]
        )

        # Pozwolenie EventBridge na wywołanie Lambdy
        try:
            self.lambda_client.add_permission(
                FunctionName=self.function_name,
                StatementId='EventBridgeInvokePermission',
                Action='lambda:InvokeFunction',
                Principal='events.amazonaws.com',
                SourceArn=f'arn:aws:events:{self.session.region_name}:{account_id}:rule/{self.rule_name}'
            )
        except ClientError as e:
            if e.response['Error']['Code'] != 'ResourceConflictException':
                raise

    def deploy(self):
        try:
            self._cleanup_existing_resources()
            role_arn = self.create_iam_role()

            # Czekamy dodatkowo na stabilizację IAM przed utworzeniem funkcji
            print("⏳ Czekam na stabilizację uprawnień IAM...")
            time.sleep(10)

            lambda_arn = self.deploy_lambda_function(role_arn)
            self.setup_eventbridge_rule(lambda_arn)

            print("\n✅✅ WDROŻENIE ZAKOŃCZONE SUKCESEM ✅✅")
            print("System autotagowania nasłuchuje na zdarzenia tworzenia zasobów.")

        except Exception as e:
            print(f"\n❌ WDROŻENIE NIEUDANE: {str(e)}")
            raise


if __name__ == '__main__':
    # Uruchomienie w regionie us-east-1 (tam gdzie masz polityki)
    deployer = AutoTaggingDeployer(region='us-east-1')
    deployer.deploy()