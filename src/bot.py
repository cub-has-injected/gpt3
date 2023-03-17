import discord
import os
from dotenv import load_dotenv
from discord import app_commands
from discord.ext.commands import cooldown, BucketType
from src.discordBot import DiscordClient, Sender
from src import responses
from src import log
from src.logger import logger
from src.chatgpt import ChatGPT, DALLE
from src.models import OpenAIModel
from src.memory import Memory
from src.server import keep_alive
from cogs.utils.config import get_config_value
import aiohttp
from src.moderation import (
    moderate_message,
    send_moderation_blocked_message,
    send_moderation_flagged_message,
)
from googleapiclient.discovery import build


load_dotenv()

logger = log.setup_logger(__name__)

models = OpenAIModel(api_key=os.getenv('OPENAI_API_KEY'), model_engine=os.getenv('OPENAI_MODEL_ENGINE'))

memory = Memory(system_message=os.getenv('SYSTEM_MESSAGE'))
chatgpt = ChatGPT(models, memory)
dalle = DALLE(models)

isPrivate = False

class aclient(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.activity = discord.Activity(type=discord.ActivityType.watching, name="on future. | /chat")


async def send_message(message, user_message):
    isReplyAll =  os.getenv("REPLYING_ALL")
    if isReplyAll == "False":
        author = message.user.id
        await message.response.defer(ephemeral=isPrivate)
    else:
        author = message.author.id
    try:
        response = (f'> **{user_message}** - <@{str(author)}' + '> \n\n')
        chat_model = os.getenv("CHAT_MODEL")
        if chat_model == "OFFICIAL":
            response = f"{response}{await responses.official_handle_response(user_message)}"
        elif chat_model == "UNOFFICIAL":
            response = f"{response}{await responses.unofficial_handle_response(user_message)}"
        char_limit = 1900
        if len(response) > char_limit:
            # Split the response into smaller chunks of no more than 1900 characters each(Discord limit is 2000 per chunk)
            if "```" in response:
                # Split the response if the code block exists
                parts = response.split("```")

                for i in range(len(parts)):
                    if i%2 == 0: # indices that are even are not code blocks
                        if isReplyAll == "True":
                            await message.channel.send(parts[i])
                        else:
                            await message.followup.send(parts[i])

                    else: # Odd-numbered parts are code blocks
                        code_block = parts[i].split("\n")
                        formatted_code_block = ""
                        for line in code_block:
                            while len(line) > char_limit:
                                # Split the line at the 50th character
                                formatted_code_block += line[:char_limit] + "\n"
                                line = line[char_limit:]
                            formatted_code_block += line + "\n"  # Add the line and seperate with new line

                        # Send the code block in a separate message
                        if (len(formatted_code_block) > char_limit+100):
                            code_block_chunks = [formatted_code_block[i:i+char_limit]
                                                 for i in range(0, len(formatted_code_block), char_limit)]
                            for chunk in code_block_chunks:
                                if isReplyAll == "True":
                                    await message.channel.send(f"```{chunk}```")
                                else:
                                    await message.followup.send(f"```{chunk}```")
                        elif isReplyAll == "True":
                            await message.channel.send(f"```{formatted_code_block}```")
                        else:
                            await message.followup.send(f"```{formatted_code_block}```")

            else:
                response_chunks = [response[i:i+char_limit]
                                   for i in range(0, len(response), char_limit)]
                for chunk in response_chunks:
                    if isReplyAll == "True":
                        await message.channel.send(chunk)
                    else:
                        await message.followup.send(chunk)
        elif isReplyAll == "True":
            await message.channel.send(response)
        else:
            await message.followup.send(response)
    except Exception as e:
        if isReplyAll == "True":
            await message.channel.send("> **Error: We caught some an error. Please try again.**")
        else:
            await message.followup.send("> **Error: We caught some an error. Please try again.**")
        logger.exception(f"Error while sending message: {e}")


async def send_start_prompt(client):
    import os.path

    config_dir = os.path.abspath(f"{__file__}/../../")
    prompt_name = 'starting-prompt.txt'
    prompt_path = os.path.join(config_dir, prompt_name)
    discord_channel_id = os.getenv("DISCORD_CHANNEL_ID")
    try:
        if os.path.isfile(prompt_path) and os.path.getsize(prompt_path) > 0:
            with open(prompt_path, "r", encoding="utf-8") as f:
                prompt = f.read()
                if (discord_channel_id):
                    logger.info(f"Send starting prompt with size {len(prompt)}")
                    chat_model = os.getenv("CHAT_MODEL")
                    response = ""
                    if chat_model == "OFFICIAL":
                        response = f"{response}{await responses.official_handle_response(prompt)}"
                    elif chat_model == "UNOFFICIAL":
                        response = f"{response}{await responses.unofficial_handle_response(prompt)}"
                    channel = client.get_channel(int(discord_channel_id))
                    await channel.send(response)
                    logger.info(f"Starting prompt response:{response}")
                else:
                    logger.info("You didn't chose an channel Skipping.")
        else:
            logger.info(f"No info about {prompt_name}. Skipping.")
    except Exception as e:
        logger.exception(f"Oops! That's the error: {e}")


def run_discord_bot():
    client = aclient()

    @client.event
    async def on_ready():
        await send_start_prompt(client)
        await client.tree.sync()
        logger.info(f'{client.user} is now running!')
    

    @client.tree.command(name="chat", description="Talk with GPT-3!")
    @cooldown(1, 10, BucketType.user)
    async def chat(interaction: discord.Interaction, *, message: str):
        isReplyAll =  os.getenv("REPLYING_ALL")
        if isReplyAll == "True":
            await interaction.response.defer(ephemeral=False)
            await interaction.followup.send(
                "> **Warn: You already on replyAll mode. If you want to use slash command, switch to normal mode, use `/replyall` again**")
            logger.warning("\x1b[31mYou already on replyAll mode, can't use slash command!\x1b[0m")
            return
        if interaction.user == client.user:
            return
        username = str(interaction.user)
        user_message = message
        channel = str(interaction.channel)
        logger.info(
            f"\x1b[31m{username}\x1b[0m : '{user_message}' ({channel})")
        await send_message(interaction, user_message)
        
    sender = Sender()

    @client.tree.command(name="imagine", description="Generate any image with DALL-E 2.0!")
    @cooldown(1, 15, BucketType.user)
    async def imagine(interaction: discord.Interaction, *, prompt: str):
        if interaction.user.id == 1:
            await interaction.response.send_message(
               "> **Error: You are banned from using this command!**")
            return
        else:
            #if interaction.user == client.user:
            if interaction.user != client.user:
                await interaction.response.defer()
                image_url = dalle.generate(prompt)
                await sender.send_image(interaction, prompt, image_url)
                return

    @client.tree.command(name="private", description="Make GPT-3 prompts private.")
    async def private(interaction: discord.Interaction):
        global isPrivate
        await interaction.response.defer(ephemeral=False)
        if not isPrivate:
            isPrivate = not isPrivate
            logger.warning("\x1b[31mSwitch to private mode\x1b[0m")
            await interaction.followup.send(
                "> **Info: Next message will be sent via private mode. If you want switch to public, then use `/public`**")
        else:
            logger.info("You already on private mode!")
            await interaction.followup.send(
                "> **Warn: You are already using the private mode. If you want switch to public, then use `/public`**")

    @client.tree.command(name="public", description="Включить публичный режим к вашим GPT-3 запросам.")
    async def public(interaction: discord.Interaction):
        global isPrivate
        await interaction.response.defer(ephemeral=False)
        if isPrivate:
            isPrivate = not isPrivate
            await interaction.followup.send(
                "> **Info: Next message will be sent via public mode. If you want switch to private, then use `/private`**")
            logger.warning("\x1b[31mSwitch to public mode\x1b[0m")
        else:
            await interaction.followup.send(
                "> **Warn: You are already using the public mode. If you want switch to private, then use `/private`**")
            logger.info("You already on public mode!")        
            
    @client.tree.command(name="reset", description="Clear all your prompts from GPT-3 history.")
    async def reset(interaction: discord.Interaction):
        chat_model = os.getenv("CHAT_MODEL")
        if chat_model == "OFFICIAL":
            responses.chatbot.reset()
        elif chat_model == "UNOFFICIAL":
            responses.chatbot.reset_chat()
        await interaction.response.defer(ephemeral=False)
        await interaction.followup.send("> **Info: I have forgotten everything! Ask me again.**")
        logger.warning(
            "\x1b[31mChatGPT bot has been successfully reset\x1b[0m")
        await send_start_prompt(client)

    @client.tree.command(name="help", description="Commands and help.")
    async def help(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        await interaction.followup.send(""":pineapple: **Commands:** \n
        :warning: Please don't violate GPT-3 Terms Of Service, else we blocking you from using GPT-3.
        - `/chat [Ваш запрос]` Talk with GPT-3!
        - `/imagine [Ваш запрос]` Generate any image with DALL-E 2.0!
        - `/public` Switch GPT-3 to public mode.
        - `/private` Switch GPT-3 to private mode.
        - `/reset` Clear GPT-3 history with your prompts to make results better.\n""")
        logger.info(
            "\x1b[31mSomeone need help!\x1b[0m")

    @client.event
    async def on_message(message):
        isReplyAll =  os.getenv("REPLYING_ALL")
        if isReplyAll == "True" and message.channel.id == int(os.getenv("REPLYING_ALL_DISCORD_CHANNEL_ID")):
            if message.author == client.user:
                return
            username = str(message.author)
            user_message = str(message.content)
            channel = str(message.channel)
            logger.info(f"\x1b[31m{username}\x1b[0m : '{user_message}' ({channel})")
            await send_message(message, user_message)

    async def send_image(self, interaction, send, receive):
        try:
            user_id = interaction.user.id
            response = f'> **{send}** - <@{str(user_id)}> \n\n'
            await interaction.followup.send(response)
            await interaction.followup.send(receive)
            logger.info(f"{user_id} sent: {send}, response: {receive}")
        except Exception as e:
            await interaction.followup.send('> **Error: We got an error while generating image from DALL-E!**')
            logger.exception(f"Error while sending:{send} in DALL-E, error: {e}")

    TOKEN = os.getenv("DISCORD_BOT_TOKEN")

    client.run(TOKEN)
