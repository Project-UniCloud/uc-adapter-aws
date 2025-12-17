# AWS Cloud Adapter

**AWS Cloud Adapter** is a gRPC-based middleware designed to manage AWS cloud environments for educational purposes. It automates the creation of student groups, enforces security policies based on tags (ABAC), manages costs, and performs automated resource cleanup.

---

## Key Concepts & Architecture
## Project Structure

The project follows a modular architecture based on domain logic (IAM, Cost, Resources).

```text
aws-cloud-adapter/
├── common/                     # Shared utilities
│   ├── logger.py               # Centralized logging configuration
│   └── naming.py               # Name normalization logic
│
├── config/                     # Configuration & Policies
│   ├── policy_manager.py       # JSON Policy loader
│   ├── system_health.py        # Self-Healing & Diagnostic logic
│   ├── automation/
│   │   └── auto-tagging/       # Infrastructure as Code (Lambda + EventBridge)
│   │       ├── deploy_auto_tagging.py
│   │       └── lambda_function.py
│   └── policies/               # IAM Policy JSON templates
│       ├── change_password_policy.json
│       ├── regional_restriction_policy.json
│       ├── student_ec2_policy.json
│       └── ...
│
├── cost/                       # Cost Management
│   └── cost_manager.py         # AWS Cost Explorer integration
│
├── iam/                        # Identity & Access Management
│   ├── group_manager.py        # Group creation & Policy assignment
│   └── user_manager.py         # User creation
│
├── resources/                  # Resource Management
│   └── resource_cleaner.py     # Resource Groups Tagging API & Cleanup logic
│
├── proto/                      # gRPC Definition Submodule
│   └── adapter_interface.proto
│
├── adapter_interface_pb2.py    # Generated gRPC code (do not edit)
├── adapter_interface_pb2_grpc.py
├── generate_proto_files.bat    # Script to compile .proto files
├── main.py                     # Entry point (gRPC Server + Bootstrap)
├── requirements.txt
└── README.md
```
### 1. Tagging Strategy (The Core Logic)
This system relies entirely on **Attribute-Based Access Control (ABAC)** using specific tags. Without these tags, the system cannot track costs or enforce permissions.

* **`CreatedBy`**: Contains the IAM Username of the creator (e.g., `student1`).
    * *Purpose:* Allows students to manage **only their own resources**.
* **`Group`**: Contains the name of the laboratory group (e.g., `LabGroupA`).
    * *Purpose:* Allows the **Resource Cleaner** to find and wipe resources at the end of a class. Allows **Leaders** (Teachers) to manage all resources belonging to their group. Used by **AWS Cost Explorer** to track spending per subject.

### 2. Permission Model (Student vs. Leader)
We utilize a strict permissions model to ensure isolation:

* **Students:**
    * Can **SEE** (List/Describe) all resources in the account (required for console functionality).
    * Can **MODIFY/DELETE** only resources where the tag `CreatedBy` matches their own `aws:username`.
    * *Constraint:* They are forced to work within the `us-east-1` region.
* **Leaders (Teachers):**
    * Can **SEE** all resources.
    * Can **MODIFY/DELETE** any resource where the tag `Group` matches their assigned group.
    * Have additional permissions (e.g., viewing logs, billing data).

### 3. Dual-Group Infrastructure
When you create a group (e.g., `"PythonLab"`), the adapter actually creates **two** IAM Groups in AWS:

1.  **Student Group (`PythonLab`):** Contains standard policies for students.
2.  **Leader Group (`Leaders-PythonLab`):** Contains elevated privileges.

**Why two groups?**
1.  **Permission Separation:** Leaders need to see what students see, plus extra administrative rights. Leaders are added to **BOTH** groups.
2.  **AWS Limits:** AWS imposes strict limits on the size of Inline Policies. Separating the logic prevents hitting the 10KB character limit per group.

---

## System Health Check & Self-Healing

Every time the adapter starts, it runs a comprehensive diagnostic suite. If critical components are missing, it attempts to **Auto-Repair** them.

**What is checked?**
1.  **AWS Connectivity:** Verifies credentials and connection to `us-east-1`.
2.  **Local Configuration:** Ensures all policy JSON files exist locally.
3.  **Account Password Policy:** Checks if the AWS Account allows users to change their own passwords. If not, it updates the account policy (Critical for new students).
4.  **Cost Allocation Tags:** Checks if the `Group` tag is active in the AWS Billing Console. If inactive, it sends an activation request.
5.  **Infrastructure (Auto-Tagging):**
    * Checks if the **Auto-Tagging Lambda** exists.
    * Checks if the **EventBridge Rule** is active and targeting the Lambda.
    * *Auto-Remediation:* If the Lambda or Rule was deleted manually, the adapter re-deploys them automatically.

---

## Installation & Setup

### Prerequisites
* Python 3.10+
* An AWS Account with Administrator Access.
* Git (with submodules support).

### 1. Clone the Repository
```bash
git clone --recurse-submodules <REPO_URL>
cd aws-cloud-adapter
```
### 2. Set up Environment
Create a virtual environment and install dependencies:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```
### 3. Configure AWS Credentials
Create a .env file in the root directory:
```bash
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
```
Note: The adapter enforces the us-east-1 region for most global operations and IAM/Billing consistency.
### 4. Generate gRPC Code
Before running the application, you must generate the Python code from the ```.proto```` definitions. Run the provided script:
```bash
.\generate_proto_files.bat
```
### 5. Run the Adapter
```bash
python main.py
```
On the first run, the system may take a few seconds to automatically deploy the Auto-Tagging infrastructure (Lambda, Roles, Rules) to your AWS account.

### Developer Guide: Adding New Services
If you want to enable a new AWS service (e.g., SNS) for students, you must update three distinct parts of the system. Failing to do so will result in permission errors or "zombie" resources that cannot be deleted.

**Step 1: Create IAM Policies (The Permission Layer)**
You must create **TWO** policy files in ```bash config/policies/.``` If you miss one, the service will not be visible in the available services list.
1. ```bash student_sns_policy.json```:
   * Allow actions (e.g., ``` bash sns:CreateTopic```, ```bash sns:Publish```).
   * **Crucial:** Add a ```Condition``` ensuring they can only modify resources tagged with their username.
2. ```leader_sns_policy.json```:
   * Allow actions for the leader (usually broader access).
   * Add a ```Condition``` matching the ``` Group``` tag.

**Step 2: Update Auto-Tagging (The Cost Layer)**
The system relies on a Lambda function to apply tags immediately upon resource creation.
1. Open ``` config/automation/auto-tagging/deploy_auto_tagging.py```
2. Add the new service events (e.g., ``` sns:CreateTopic``` to the **EventBridge Pattern**
3. Open ```config/automation/auto-tagging/lambda_function.py```
4. Add logic to handle the specific response format of the new service to apply the ```Group``` and ```CreatedBy``` tags.

**Step 3: Update Resource Cleaner (The Cleanup Layer)**
To ensure the ```RemoveGroup``` command wipes everything:
1. Open ```resources/resource_cleaner.py```
2. Locate the ```delete_resource``` function
3. Add an ```elif service == "sns":``` block
4. Implement the ```boto3``` logic to delete the resource (e.g., ```delete_topic```
---

### Cost Management Note
For the adapter to track costs per group:
1. The **Auto-Tagging Lambda** must be working (checked automatically by Health Check).
2. **Cost Allocation Tags** must be active in the Billing Console.
   * Note: The Health Check attempts to activate the Group tag automatically. However, AWS takes up to **24 hours** to propagate cost data for newly activated tags. Do not panic if costs show $0.00 on the first day.

---
### Troubleshooting
* **"Offline Mode"** warning in console: Check your ```.env``` file and internet connection. The adapter has disabled cloud operations to prevent errors.
* **Students cannot change passwords:** Restart the adapter. The Health Check will re-apply the Account Password Policy.
* **Resources are not being deleted:** Check if the resource has the ```Group``` tag. If the Auto-Tagging Lambda failed or was deployed after the resource was created, the resource is "invisible" to the cleaner. You must tag it manually or delete it manually via AWS Console.