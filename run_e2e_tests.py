import unittest
import grpc
import boto3
import time
import logging
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

# Import gRPC stubs
import adapter_interface_pb2 as pb2
import adapter_interface_pb2_grpc as pb2_grpc

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] TEST: %(message)s'
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
GRPC_SERVER_ADDR = 'localhost:50051'
TEST_GROUP_NAME = 'E2E-Automated-Test-Group'
TEST_LEADERS = ['Leader-E2E']
TEST_STUDENTS = ['Student-E2E-1', 'Student-E2E-2']
TEST_RESOURCES = ['s3', 'ec2']


class TestEndToEndAWS(unittest.TestCase):
    """
    Integration Tests checking the full flow:
    gRPC Request -> Logic -> AWS Change -> Verification
    """

    @classmethod
    def setUpClass(cls):
        """Runs once before all tests. Connects to gRPC and AWS."""
        # 1. Connect to gRPC
        cls.channel = grpc.insecure_channel(GRPC_SERVER_ADDR)
        cls.stub = pb2_grpc.CloudAdapterStub(cls.channel)

        # 2. Connect to AWS IAM directly (for verification)
        cls.iam_client = boto3.client('iam')

        logger.info("✅ Connected to gRPC Server and AWS IAM.")

    def setUp(self):
        """Runs before EACH test. Ensures clean state."""
        self._force_cleanup(TEST_GROUP_NAME)

    def tearDown(self):
        """Runs after EACH test. Cleans up garbage."""
        self._force_cleanup(TEST_GROUP_NAME)

    def _force_cleanup(self, group_name):
        """Helper to force delete group via gRPC to ensure clean slate."""
        try:
            req = pb2.RemoveGroupRequest(groupName=group_name)
            self.stub.RemoveGroup(req)
        except Exception:
            pass  # Ignore errors during cleanup

    # =========================================================================
    # TESTS
    # =========================================================================

    def test_01_health_check(self):
        """Test if server is responding."""
        logger.info("--- TEST: Server Health Check ---")
        response = self.stub.GetStatus(pb2.StatusRequest())
        self.assertTrue(response.isHealthy, "Server reported unhealthy status!")
        logger.info("✅ Server is Healthy.")

    def test_02_full_lifecycle(self):
        """
        Create Group -> Check AWS -> Create Users -> Check AWS -> Delete -> Check AWS
        """
        logger.info("--- TEST: Full Lifecycle (Create -> Verify -> Delete) ---")

        # 1. CREATE GROUP
        logger.info(f"1. Creating Group: {TEST_GROUP_NAME}")
        create_req = pb2.CreateGroupWithLeadersRequest(
            groupName=TEST_GROUP_NAME,
            leaders=TEST_LEADERS,
            resourceTypes=TEST_RESOURCES
        )
        resp = self.stub.CreateGroupWithLeaders(create_req)
        self.assertEqual(resp.groupName, TEST_GROUP_NAME)

        # 2. VERIFY IN AWS (The "Real" Check)
        # We verify if IAM Group actually exists using Boto3
        logger.info("2. Verifying Group existence in AWS IAM...")
        time.sleep(2)  # Give AWS a moment (eventual consistency)

        try:
            # Check Student Group
            self.iam_client.get_group(GroupName=TEST_GROUP_NAME)
            # Check Leader Group
            self.iam_client.get_group(GroupName=f"Leaders-{TEST_GROUP_NAME}")
            # Check Leader User
            user_check = f"{TEST_LEADERS[0]}-{TEST_GROUP_NAME}"  # normalized name logic applied in manager
            # Note: Assuming simple normalization here, careful with special chars
            self.iam_client.get_user(UserName=user_check)
            logger.info("✅ AWS Verification Passed: Groups and Users found in IAM.")
        except ClientError as e:
            self.fail(f"❌ AWS Verification Failed: Resource missing in cloud! {e}")

        # 3. ADD STUDENTS
        logger.info(f"3. Adding Students: {TEST_STUDENTS}")
        user_req = pb2.CreateUsersForGroupRequest(
            groupName=TEST_GROUP_NAME,
            users=TEST_STUDENTS
        )
        user_resp = self.stub.CreateUsersForGroup(user_req)
        self.assertIn("Successfully processed", user_resp.message)

        # 4. VERIFY STUDENTS IN AWS
        try:
            student_check = f"{TEST_STUDENTS[0]}-{TEST_GROUP_NAME}"
            self.iam_client.get_user(UserName=student_check)
            logger.info("✅ AWS Verification Passed: Student user found in IAM.")
        except ClientError:
            self.fail("❌ AWS Verification Failed: Student user not found!")

        # 5. REMOVE GROUP
        logger.info("5. Removing Group...")
        del_req = pb2.RemoveGroupRequest(groupName=TEST_GROUP_NAME)
        del_resp = self.stub.RemoveGroup(del_req)
        self.assertTrue(del_resp.success)

        # 6. VERIFY CLEANUP
        logger.info("6. Verifying Cleanup in AWS...")
        time.sleep(2)
        try:
            self.iam_client.get_group(GroupName=TEST_GROUP_NAME)
            self.fail("❌ Cleanup Failed: Group still exists in AWS!")
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchEntity':
                logger.info("✅ AWS Verification Passed: Group is gone.")
            else:
                self.fail(f"Error during verification: {e}")


if __name__ == '__main__':
    # Run tests nicely
    print("\n🚀 STARTING E2E INTEGRATION TESTS...")
    print("⚠️  WARNING: This will create and delete real resources in AWS.")
    print("---------------------------------------------------------------")
    unittest.main()