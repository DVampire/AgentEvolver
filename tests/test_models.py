import os
import sys
import json
from dotenv import load_dotenv
load_dotenv(verbose=True)

from pathlib import Path
import argparse
from mmengine import DictAction
import asyncio
from pydantic import BaseModel, Field
from typing import List, Dict, Any

root = str(Path(__file__).resolve().parents[1])
sys.path.append(root)

from src.config import config
from src.logger import logger
from src.model import model_manager
from src.message import (
    HumanMessage,
    SystemMessage,
    ContentPartText,
    ContentPartImage,
    ImageURL,
    ContentPartAudio,
    AudioURL,
    ContentPartVideo,
    VideoURL,
    ContentPartPdf,
    PdfURL,
)

from src.tool import tool_manager
from src.version import version_manager
from src.utils import assemble_project_path, make_file_url


async def test_chat():
    logger.info(f"| --------------------------------------------------")
    logger.info(f"| Testing chat with different models")
    models = [
        # OpenAI models
        # "openrouter/gpt-4o",
        # "openrouter/gpt-4.1",
        # "openrouter/gpt-5",
        # "openrouter/gpt-5.1",
        # "openrouter/gpt-5.2",
        # "openrouter/o3",
        # "openrouter/gpt-5.4-pro",
        # "openrouter/gpt-5.3-codex",
        # "openai/gpt-4o",
        # "openai/gpt-4.1",
        # "openai/gpt-5",
        # "openai/gpt-5.1",
        # "openai/gpt-5.2",
        # "openai/gpt-5.4-pro",
        # "openai/o3",
        # "openai/o3-mini",
        # "newapi/gpt-5.4-pro",
        # "newapi/gpt-5.4",
        # "newapi/o3-mini",
        
        # Anthropic models
        # "openrouter/claude-sonnet-3.7",
        # "openrouter/claude-sonnet-4",
        # "openrouter/claude-opus-4",
        # "openrouter/claude-sonnet-4.5",
        # "openrouter/claude-opus-4.5",
        # "openrouter/claude-sonnet-4.6",
        # "openrouter/claude-opus-4.6",
        # "anthropic/claude-sonnet-3.7",
        # "anthropic/claude-sonnet-4",
        # "anthropic/claude-sonnet-4.5",
        # "newapi/claude-opus-4.6",
        
        
        # Gemini models
        # "openrouter/gemini-2.5-flash",
        # "openrouter/gemini-2.5-pro",
        # "openrouter/gemini-3-flash-preview",
        # "openrouter/gemini-3-pro-preview",
        # "openrouter/gemini-3.1-pro-preview",
        # "openrouter/gemini-3.1-pro-preview-plugins",
        # "google/gemini-2.5-flash",
        # "google/gemini-2.5-pro",
        # "google/gemini-3-pro-preview",
        # "newapi/gemini-3.1-pro-preview",
        
        # Grok models
        # "openrouter/grok-4.1-fast",

        # int openrouter models
        # "int_openrouter/gpt-5.4",
        # "int_openrouter/gpt-5.4-pro",
        # "int_openrouter/o3-mini",
        "int_openrouter/gemini-3.1-pro-preview",
        # "int_openrouter/grok-4.1-fast",
    ]
    
    logger.info(f"| Testing Local Image.")
    logger.info(f"| --------------------------------------------------")
    image_url = make_file_url(file_path=assemble_project_path("tests/files/pokemon.jpg"))
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="What are the names of the Pokémon in the image? You can use deep research to find the answer. Please only return the names of the Pokémon."),
            ContentPartImage(image_url=ImageURL(url=image_url, detail="high")),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(
            model=model,
            messages=messages
        )
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")

    logger.info(f"| Testing Online Image.")
    logger.info(f"| --------------------------------------------------")
    image_url = "https://m.media-amazon.com/images/I/81Uuowxx0-L._AC_SX679_.jpg"
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="What are the names of the Pokémon in the image? You can use deep research to find the answer. Please only return the names of the Pokémon."),
            ContentPartImage(image_url=ImageURL(url=image_url, detail="high")),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(
            model=model,
            messages=messages
        )
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")

async def test_audio():
    logger.info(f"| --------------------------------------------------")
    logger.info(f"| Testing audio with different models")
    models = [
        "openrouter/gemini-3.1-pro-preview-plugins",
    ]
    
    logger.info(f"| Testing Local Audio.")
    logger.info(f"| --------------------------------------------------")

    audio_url = make_file_url(file_path=assemble_project_path("tests/files/audio.mp3"))

    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="Please transcribe the audio file and provide the transcription. Only return the transcription, no other text or formatting."),
            ContentPartAudio(audio_url=AudioURL(url=audio_url)),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(model=model, messages=messages)
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")

    logger.info(f"| Testing Online Audio.") # ！！！不支持audio的url
    logger.info(f"| --------------------------------------------------")
    
    audio_url = "https://www.mmsp.ece.mcgill.ca/Documents/AudioFormats/WAVE/Samples/AFsp/M1F1-Alaw-AFsp.wav"

    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="Please transcribe the audio file and provide the transcription. Only return the transcription, no other text or formatting."),
            ContentPartAudio(audio_url=AudioURL(url=audio_url)),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(model=model, messages=messages)
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")


async def test_embedding():
    logger.info(f"| --------------------------------------------------")
    logger.info(f"| Testing embedding with different models")
    models = [
        # "openai/text-embedding-3-small",
        "openai/text-embedding-3-large",
        # "openai/text-embedding-ada-002",
    ]
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="Please embed the text and provide the embedding."),
            ContentPartText(text="The text is: The quick brown fox jumps over the lazy dog."),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(model=model, messages=messages)
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")

async def test_video():
    logger.info(f"| --------------------------------------------------")
    logger.info(f"| Testing video with different models")
    models = [
        "openrouter/gemini-2.5-flash",
        # "google/gemini-2.5-flash",
    ]
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="Please analyze the video and provide the analysis. Only return the analysis, no other text or formatting."),
            ContentPartVideo(video_url=VideoURL(url=make_file_url(file_path="tests/files/video.MOV"))),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(model=model, messages=messages)
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")


async def test_pdf():
    logger.info(f"| --------------------------------------------------")
    logger.info(f"| Testing PDF with different models")
    models = [
        "openrouter/gemini-3-flash-preview-plugins"
    ]
    
    logger.info(f"| Testing Local PDF.")
    logger.info(f"| --------------------------------------------------")

    pdf_url = make_file_url(file_path=assemble_project_path("tests/files/pdf.pdf"))

    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="Please analyze the PDF and provide the analysis. Only return the analysis, no other text or formatting."),
            ContentPartPdf(pdf_url=PdfURL(url=pdf_url)),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(model=model, messages=messages)
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")

    logger.info(f"| Testing Online PDF.")
    logger.info(f"| --------------------------------------------------")

    pdf_url = "https://arxiv.org/pdf/2302.11312" # ！！！不支持pdf的url

    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="Please analyze the PDF and provide the analysis. Only return the analysis, no other text or formatting."),
            ContentPartPdf(pdf_url=PdfURL(url=pdf_url)),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(model=model, messages=messages)
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")


async def test_response_format():
    logger.info(f"| --------------------------------------------------")
    logger.info(f"| Testing response format with different models")
    models = [
        # OpenAI models
        # "openrouter/gpt-4o",
        # "openrouter/gpt-4.1",
        # "openrouter/gpt-5",
        # "openrouter/gpt-5.1",
        # "openrouter/gpt-5.2",
        # "openrouter/o3",
        "openrouter/gpt-5.3-codex",
        # "openai/gpt-4o",
        # "openai/gpt-4.1",
        # "openai/gpt-5",
        # "openai/gpt-5.1",
        # "openai/o3",
        
        # Anthropic models
        # "openrouter/claude-sonnet-3.7",
        # "openrouter/claude-sonnet-4",
        # "openrouter/claude-opus-4",
        # "openrouter/claude-sonnet-4.5",
        # "openrouter/claude-opus-4.5",
        # "anthropic/claude-sonnet-4.5",
        
        # Gemini models
        # "openrouter/gemini-2.5-flash",
        # "openrouter/gemini-2.5-pro",
        # "openrouter/gemini-3-pro-preview",
        # "openrouter/gemini-3-flash-preview",
        # "google/gemini-2.5-flash",
        # "google/gemini-2.5-pro",
        # "google/gemini-3-pro-preview",
    ]
    
    class ToolInputArgs(BaseModel):
        name: str = Field(description="The name of the tool")
        args: Dict[str, Any] = Field(description="The arguments of the tool")
    
    class ThinkOutput(BaseModel):
        thinking: str = Field(description="The thinking process of the assistant")
        previous_goal: str = Field(description="The previous goal of the assistant")
        next_goal: str = Field(description="The next goal of the assistant")
        tool: List[ToolInputArgs] = Field(description="The list of tools to call")
    
    prompt = """
    <available_tools>
    available_tools:
    <done>
    done: DoneTool
     - result: The result of the task
     - reasoning: The reasoning process of the task
    </done>
    <example>
    Example: {"name": "done", "args": {"result": "The task has been completed.", "reasoning": "The task has been completed successfully."}}
    </example>
    
    Please add the numbers 1 and 2, get the result and call the done tool with the result.
    """
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text=prompt),
        ]),
    ]
    
    for model in models:
    
        response = await model_manager(model=model, messages=messages, response_format=ThinkOutput)
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
        
        parsed_response = response.extra.parsed_model
        print(parsed_response)
        
    logger.info(f"| --------------------------------------------------")

async def test_tool_calling():
    logger.info(f"| --------------------------------------------------")
    logger.info(f"| Testing tool calling with different models")
    models = [
        # OpenAI models
        "openrouter/gpt-4o",
        "openrouter/gpt-4.1",
        "openrouter/gpt-5",
        "openrouter/gpt-5.1",
        "openrouter/gpt-5.2",
        "openrouter/o3",
        # "openai/gpt-4o",
        # "openai/gpt-4.1",
        # "openai/gpt-5",
        # "openai/gpt-5.1",
        # "openai/o3",
        
        # Anthropic models
        # "openrouter/claude-sonnet-3.7",
        # "openrouter/claude-sonnet-4",
        # "openrouter/claude-opus-4",
        # "openrouter/claude-sonnet-4.5",
        # "openrouter/claude-opus-4.5",
        # "anthropic/claude-sonnet-3.7",
        # "anthropic/claude-sonnet-4",
        # "anthropic/claude-sonnet-4.5",
        
        # Gemini models
        # "openrouter/gemini-2.5-flash",
        # "openrouter/gemini-2.5-pro",
        # "openrouter/gemini-3-pro-preview",
        # "google/gemini-2.5-flash",
        # "google/gemini-2.5-pro",
        # "google/gemini-3-pro-preview",
    ]
    
    tools = [
        await tool_manager.get('bash'),
    ]
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="Please run the command 'ls -l' and return the output."),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(
            model=model, 
            messages=messages,
            tools=tools,
        )
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")

async def test_search():
    logger.info(f"| --------------------------------------------------")
    logger.info(f"| Testing search with different models")
    models = [
        "openrouter/gemini-3.1-pro-preview-plugins",
        # "openrouter/gemini-3-flash-preview-plugins"
    ]
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="Please search the web for the latest news about the AAPL stock."),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(model=model, messages=messages)
        logger.info(f"| {model} Response: {json.dumps(response.message, indent=4)}")
    logger.info(f"| --------------------------------------------------")


async def test_music():
    logger.info(f"| --------------------------------------------------")
    logger.info(f"| Testing music with different models")
    models = [
        "openrouter/gemini-3.1-pro-preview-plugins",
    ]
    
    logger.info(f"| Testing Local Image.")
    logger.info(f"| --------------------------------------------------")
    image_url = make_file_url(file_path=assemble_project_path("tests/files/0075.png"))
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=[
            ContentPartText(text="Search the web then guess the music."),
            ContentPartImage(image_url=ImageURL(url=image_url, detail="high")),
        ]),
    ]
    
    for model in models:
        logger.info(f"| Testing {model}")
        response = await model_manager(
            model=model,
            messages=messages
        )
        logger.info(f"| {model} Response: {json.dumps(response.model_dump(), indent=4)}")
    logger.info(f"| --------------------------------------------------")


def parse_args():
    parser = argparse.ArgumentParser(description='main')
    parser.add_argument("--config", default=os.path.join(root, "configs", "bus.py"), help="config file path")

    parser.add_argument(
        '--cfg-options',
        nargs='+',
        action=DictAction,
        help='override some settings in the used config, the key-value pair '
        'in xxx=yyy format will be merged into config file. If the value to '
        'be overwritten is a list, it should be like key="[a,b]" or key=a,b '
        'It also allows nested list/tuple values, e.g. key="[(a,b),(c,d)]" '
        'Note that the quotation marks are necessary and that no white space '
        'is allowed.')
    args = parser.parse_args()
    return args

async def main():
    args = parse_args()
    
    config.initialize(config_path=args.config, args=args)
    logger.initialize(config=config)
    logger.info(f"| Config: {config.pretty_text}")

    # Initialize version manager
    await version_manager.initialize()
    logger.info(f"| Version manager initialized: {await version_manager.list()}")
    
    # Initialize model manager
    await model_manager.initialize()
    logger.info(f"| Model manager initialized: {model_manager.list()}")
    
    # Initialize tools
    await tool_manager.initialize(tool_names=config.tool_names)
    logger.info(f"| Tools initialized: {await tool_manager.list()}")

    await test_chat()
    # await test_response_format()
    # await test_tool_calling()
    # await test_audio()
    # await test_embedding()
    # await test_video()
    # await test_pdf()
    # await test_search()
    # await test_music()

if __name__ == "__main__":
    asyncio.run(main())