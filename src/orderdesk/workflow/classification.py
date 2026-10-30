from orderdesk.workflow.state import WorkflowState
from orderdesk.prompts.classification import CLASIFICATION_SYSTEM_PROMPT, CLASSIFICATION_USER_PROMPT
from orderdesk.llm.client import llm_classification


sys_prompt = CLASIFICATION_SYSTEM_PROMPT
user_propt =  CLASIFICATION_SYSTEM_PROMPT.format()

def classify_email(workflowState: WorkflowState):

    