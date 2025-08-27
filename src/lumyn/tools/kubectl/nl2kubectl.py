# Copyright contributors to the ITBench project. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import logging
import os
import re
import subprocess
from typing import Any, Dict, Optional, Type
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client
from crewai.tools.base_tool import BaseTool
from pydantic import BaseModel, Field
from lumyn.tools.linting.kubectl_linter import KubectlLinter
from lumyn.config.tools import NL2KubectlCustomToolInputPrompt, NL2KubectlCustomToolPrompt, NL2KubectlSystemPrompt, NL2KubectlPrompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

ENV_MCP_SERVER_URL = "MCP_SERVER_URL"
ENV_BEARER_AUTH_TOKEN = "BEARER_AUTH_TOKEN"
ENV_KUBECTL_TOOL_NAME = "KUBECTL_TOOL_NAME"

MCP_SERVER_URL = os.environ.get(ENV_MCP_SERVER_URL, "http://localhost:8000/sse")
BEARER_AUTH_TOKEN = os.environ.get(ENV_BEARER_AUTH_TOKEN, None)
KUBECTL_TOOL_NAME = os.environ.get(ENV_KUBECTL_TOOL_NAME, "kubectl-executor")


class NL2KubectlCustomToolInput(BaseModel):
    nl_query: str = Field(
        title="NL Query",
        description=NL2KubectlCustomToolInputPrompt,
    )

class NL2KubectlCustomTool(BaseTool):
    name: str = "NL2Kubectl Tool"
    description: str = NL2KubectlCustomToolPrompt
    llm_backend: Any
    is_remediation: bool = False
    god_mode: bool = False
    cache_function: bool = False
    args_schema: Type[BaseModel] = NL2KubectlCustomToolInput
    output_limit: int = 8000

    def _run(self, nl_query: str) -> str:
        harmful_commands = ["rm ", "kubectl edit ", "kubectl proxy ", "kubectl port-forward ", "kubectl exec -it ", "kubectl attach -i ", "kubectl run -i ", "kubectl port-forward ", "kubectl cp "]
        if self.is_remediation:
            while True:
                try:
                    command = self._generate_kubectl_command(prompt=nl_query)
                    print(command)
                    user_input = input("Execute command? (Y/N)").strip().lower()
                    
                    while user_input not in ["y", "n"]:
                        print("Please enter 'Y' or 'N'.")
                        user_input = input("Execute command? (Y/N)").strip().lower()
                    
                    if user_input == "y":
                        for harmful_command in harmful_commands:
                            if command.startswith(harmful_command):
                                return f"Potentially harmful command found. Execution is not allowed. The harmful command was: {command}. Do not use commands that will create an interactive shell."
                        return self._execute_kubectl_command(command)[0][0:self.output_limit]
                    else:
                        problem_description = input("What is wrong with the command?")
                        retry = (
                            f"User says there is a problem with the command that you wrote:\nhere is the problem description: {problem_description}\nHere is the command that you wrote: {command}\nPlease write a new command that fixes the problem."
                        )
                        return retry
                except Exception as exc:
                    logger.error(f"NL2Kubectl Tool failed with: {exc}")
                    return f"NL2Kubectl Tool failed with: {exc}"
        else:
            try:
                    command = self._generate_kubectl_command(prompt=nl_query)
                    for harmful_command in harmful_commands:
                        if command.startswith(harmful_command):
                            return f"Potentially harmful command found. Execution is not allowed. The harmful command was: {command}"
                    return self._execute_kubectl_command(command)[0][0:self.output_limit]
            except Exception as exc:
                    logger.error(f"NL2Kubectl Tool failed with: {exc}")
                    return f"NL2Kubectl Tool failed with: {exc}"

    def _generate_kubectl_command(self, prompt: str) -> str:
        response = self.llm_backend.inference(NL2KubectlSystemPrompt, NL2KubectlPrompt + prompt)
        command_of_interest = re.search(r'```bash\n(.*?)\n```', response, re.DOTALL).group(1).strip()
        logger.info(f"NL2Kubectl Tool NL prompt received: {prompt}")
        logger.info(f"NL2Kubectl Tool response received: {response}")
        logger.info(f"NL2Kubectl Tool command returned: {command_of_interest}")
        print(f"NL2Kubectl Tool NL prompt received: {prompt}")
        print(f"NL2Kubectl Tool NL response received: {response}")
        print(f"NL2Kubectl Tool command returned: {command_of_interest}")
        return command_of_interest

    async def _run_execute_kubectl_command(self, command: str): #-> Optional[Dict[str, Any]]:
        headers = None
        if BEARER_AUTH_TOKEN:
            headers = {"Authorization": f"Bearer {BEARER_AUTH_TOKEN}"}
        
        async with sse_client(MCP_SERVER_URL, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("started call tools--------------")
                result = await session.call_tool(
                    name=KUBECTL_TOOL_NAME,
                    arguments={"command": command})
                print(result)
                print("ended call tools--------------")
                return result.content

    def _execute_kubectl_command(self, command: str): #-> Optional[Dict[str, Any]]:
        results=asyncio.run(self._run_execute_kubectl_command(command))
        stdout=results[0].text
        returncode=int(results[1].text)
        return stdout, returncode

    def _summarize_kubernetes(self, kubernetes):
        system_prompt = "You do kubectl output analysis and summarization. Look at the kubectl output given to you and provide a brief summary and analysis of them."
        kubernetes_summary = self.llm_backend.inference(system_prompt, kubernetes)
        return kubernetes_summary
