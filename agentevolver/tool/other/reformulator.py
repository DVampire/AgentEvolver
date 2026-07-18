"""Reformulator tool for reformulating final answers from agent conversations."""
from typing import List, Dict, Any, Optional
from pydantic import Field, BaseModel

from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType
from agentevolver.message.types import SystemMessage, HumanMessage
from agentevolver.model import model_manager
from agentevolver.logger import logger
from agentevolver.utils import dedent
from agentevolver.registry import TOOL


_DESCRIPTION = "Reformulate a clean, formatted final answer from an agent conversation transcript."

_INSTRUCTION = """
## Function
Reformulate a clean, formatted final answer from an agent conversation transcript.

## Guidance
- Takes the original task and the conversation history, then uses an LLM to extract and format the final answer.
- Use this tool when you need to produce a clean, formatted final answer from a conversation transcript.

## Parameters
- task (str): The original task/question that was asked.
- data (List[str]): Conversation history in the form of a list of message texts.

## Example
{"name": "reformulator_tool", "args": {"task": "What is the capital of France?", "data": ["The capital of France is Paris.", "The capital of France is Paris."]}}
"""


class ReformulatedAnswer(BaseModel):
    """Response format for reformulated final answer."""
    final_answer: str = Field(description="The final answer extracted from the conversation. Should be a number OR as few words as possible OR a comma separated list of numbers and/or strings. Must adhere to any formatting instructions specified in the original question.")

@TOOL.register_module(force=True)
class ReformulatorTool(Tool):
    """A tool for reformulating final answers from agent conversations."""
    
    name: str = "reformulator_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    
    model_name: str = Field(default="openrouter/gemini-3-flash-preview", description="The model to use for reformulation.")
    
    def __init__(self, model_name: Optional[str] = None, enable_evolving: bool = False, **kwargs):
        """
        Initialize the reformulator tool.
        
        Args:
            model_name: The model to use for reformulation. Default: "openrouter/gemini-3-flash-preview"
        """
        super().__init__(enable_evolving=enable_evolving, **kwargs)
        if model_name:
            self.model_name = model_name
    
    async def __call__(
        self, 
        task: str, 
        data: List[str],
        **kwargs
    ) -> Response:
        """
        Reformulate the final answer from a conversation transcript.
        
        Args:
            task: The original task/question that was asked
            data: Conversation history in the form of a list of message texts.
        Returns:
            Response with the reformulated final answer
        """
        try:
            
            # Build system message
            system_prompt = dedent(f"""
                You are a helpful assistant that reformulates the final answer from a conversation transcript.
            """)
            
            data_string = '\n'.join(data)
            
            agent_message_prompt = dedent(f"""
                Original task:
                {task}
                                                        
                Conversation history:
                {data_string}
                
                Extract and format the final answer from the conversation history above.
                
                Instructions for formatting the final answer:
                - Your FINAL ANSWER should be a number OR as few words as possible OR a comma separated list of numbers and/or strings.
                - ADDITIONALLY, your FINAL ANSWER MUST adhere to any formatting instructions specified in the original question (e.g., alphabetization, sequencing, units, rounding, decimal places, etc.)
                - You MUST pay attention to the required units of the calculation result. For example, if the question asks "how many thousand hours...", then the answer `1000 hours` should be `1`, not `1000`.
                - You MUST pay attention to extracting key stage names, personal names, and location names when the task required.
                - If you are asked for a number, express it numerically (i.e., with digits rather than words), don't use commas, and DO NOT INCLUDE UNITS such as $ or USD or percent signs unless specified otherwise.
                - If you are asked for a string, don't use articles or abbreviations (e.g. for cities), unless specified otherwise. Don't output any final sentence punctuation such as '.', '!', or '?'.
                - If you are asked for a comma separated list, apply the above rules depending on whether the elements are numbers or strings.
                - If you are unable to determine the final answer, output 'Unable to determine'
            
                Reformulated answer is:
            """)
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=agent_message_prompt)
            ]
            
            # Call model with response_format
            response = await model_manager(
                name=self.model_name,
                input={"messages": messages, "response_format": ReformulatedAnswer},
            )
            
            if not response.success:
                return Response(type=ResponseType.TOOL, 
                    success=False,
                    message=f"Failed to reformulate answer: {response.message}"
                )
            
            # Extract final answer from structured response
            if response.parsed_model:
                reformulated_answer = response.parsed_model
                final_answer = reformulated_answer.final_answer
                logger.info(f"> Reformulated answer: {final_answer}")

                message = final_answer
                return Response(type=ResponseType.TOOL, 
                    success=True,
                    message=message,
                    data={"original_response": response.message},
                    parsed_model=reformulated_answer
                )
            else:
                # Fallback: parse from text response
                response_text = str(response.message)
                if "FINAL ANSWER: " in response_text:
                    final_answer = response_text.split("FINAL ANSWER: ")[-1].strip()
                else:
                    final_answer = response_text.strip()

                logger.info(f"> Reformulated answer (fallback): {final_answer}")

                message = final_answer

                return Response(type=ResponseType.TOOL, 
                    success=True,
                    message=message,
                    data={"original_response": response_text}
                )
            
        except Exception as e:
            logger.error(f"Error in reformulator tool: {e}")
            return Response(type=ResponseType.TOOL, 
                success=False,
                message=f"Error reformulating answer: {str(e)}"
            )

