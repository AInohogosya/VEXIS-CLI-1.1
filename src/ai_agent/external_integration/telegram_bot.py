"""
Telegram Bot Integration for VEXIS-CLI AI Agent
Handles Telegram bot communication and message management
"""

import asyncio
import threading
import time
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from functools import wraps

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

from ..utils.logger import get_logger
from ..utils.config import load_config


def retry_on_network_error(max_retries: int = 3, initial_delay: float = 1.0, backoff_factor: float = 2.0):
    """
    Decorator to retry network operations with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        backoff_factor: Multiplier for delay after each retry (exponential backoff)
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger = get_logger("telegram_bot")
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    # Check if it's a network-related error
                    error_msg = str(e).lower()
                    is_network_error = any(
                        keyword in error_msg 
                        for keyword in ['timeout', 'network', 'connection', 'timed out', 'unreachable']
                    )
                    
                    if not is_network_error or attempt == max_retries:
                        # Not a network error or max retries reached, raise the exception
                        logger.error(f"Error in {func.__name__}: {e}")
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.warning(
                        f"Network error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f} seconds..."
                    )
                    await asyncio.sleep(delay)
            
            # If we get here, all retries failed
            raise last_exception
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger = get_logger("telegram_bot")
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    # Check if it's a network-related error
                    error_msg = str(e).lower()
                    is_network_error = any(
                        keyword in error_msg 
                        for keyword in ['timeout', 'network', 'connection', 'timed out', 'unreachable']
                    )
                    
                    if not is_network_error or attempt == max_retries:
                        # Not a network error or max retries reached, raise the exception
                        logger.error(f"Error in {func.__name__}: {e}")
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.warning(
                        f"Network error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f} seconds..."
                    )
                    time.sleep(delay)
            
            # If we get here, all retries failed
            raise last_exception
        
        # Return appropriate wrapper based on whether function is async
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


class TelegramMode(Enum):
    """Telegram bot mode"""
    NORMAL = "normal"
    TELEGRAM = "telegram"


@dataclass
class ConversationHistory:
    """Conversation history for Telegram mode"""
    user_id: int
    messages: List[Dict[str, str]] = field(default_factory=list)
    max_length: int = 50
    
    def add_message(self, role: str, content: str):
        """Add a message to the conversation history"""
        self.messages.append({"role": role, "content": content})
        # Trim to max length
        if len(self.messages) > self.max_length:
            self.messages = self.messages[-self.max_length:]
    
    def get_history(self) -> List[Dict[str, str]]:
        """Get the conversation history"""
        return self.messages
    
    def clear(self):
        """Clear the conversation history"""
        self.messages = []
    
    def format_for_prompt(self) -> str:
        """Format conversation history for inclusion in prompts"""
        if not self.messages:
            return ""
        
        formatted = "Conversation History:\n"
        for msg in self.messages:
            formatted += f"{msg['role']}: {msg['content']}\n"
        return formatted


class TelegramBotManager:
    """
    Manages Telegram bot integration for AI agent
    
    Handles:
    - Bot initialization and message receiving
    - Conversation history management
    - Message sending
    - /reset command handling
    """
    
    def __init__(self, bot_token: str, allowed_user_ids: Optional[List[int]] = None, max_history_length: int = 50, terminal_history=None):
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids or []
        self.max_history_length = max_history_length
        self.logger = get_logger("telegram_bot")
        self.terminal_history = terminal_history
        
        # Conversation history per user
        self.conversation_histories: Dict[int, ConversationHistory] = {}
        
        # Callback for processing messages
        self.message_callback: Optional[Callable[[str, int], str]] = None
        
        # Track running tasks per user for cancellation
        self._current_tasks: Dict[int, asyncio.Task] = {}
        self._task_lock = asyncio.Lock()
        
        # Application instance
        self.application: Optional[Application] = None
        
        # Running state
        self.is_running = False
        
        # Message queue for sending messages from synchronous context
        self.message_queue: List[Tuple[int, str]] = []
        
        # Background thread for processing message queue
        self.queue_processor_thread: Optional[threading.Thread] = None
        self.queue_processor_running = False
        
        # Check if telegram is available
        if not TELEGRAM_AVAILABLE:
            self.logger.error("python-telegram-bot not installed. Install with: pip install python-telegram-bot>=21.0.0")
    
    def set_message_callback(self, callback: Callable[[str, int], str]):
        """Set the callback function for processing messages"""
        self.message_callback = callback
    
    def get_conversation_history(self, user_id: int) -> ConversationHistory:
        """Get or create conversation history for a user"""
        if user_id not in self.conversation_histories:
            self.conversation_histories[user_id] = ConversationHistory(
                user_id=user_id,
                max_length=self.max_history_length
            )
        return self.conversation_histories[user_id]
    
    def clear_conversation_history(self, user_id: int):
        """Clear conversation history for a user"""
        if user_id in self.conversation_histories:
            self.conversation_histories[user_id].clear()
            self.logger.info(f"Cleared conversation history for user {user_id}")
    
    @retry_on_network_error(max_retries=10, initial_delay=1.0, backoff_factor=2.0)
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user_id = update.effective_user.id
        
        if not self._is_user_allowed(user_id):
            await update.message.reply_text("Sorry, you are not authorized to use this bot.")
            return
        
        await update.message.reply_text(
            "🤖 VEXIS-CLI AI Agent\n\n"
            "Send me commands and I'll execute them on your computer.\n"
            "Use /reset to clear conversation history.\n"
            "Use /help for more information."
        )
    
    @retry_on_network_error(max_retries=10, initial_delay=1.0, backoff_factor=2.0)
    async def reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /reset command"""
        user_id = update.effective_user.id
        
        if not self._is_user_allowed(user_id):
            return
        
        # Clear conversation history
        self.clear_conversation_history(user_id)
        
        # Clear terminal history
        if self.terminal_history:
            self.terminal_history.clear_session()
            self.logger.info(f"Cleared terminal history for user {user_id}")
        
        await update.message.reply_text("✅ Conversation history and terminal logs cleared.")
    
    @retry_on_network_error(max_retries=10, initial_delay=1.0, backoff_factor=2.0)
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        user_id = update.effective_user.id
        
        if not self._is_user_allowed(user_id):
            return
        
        await update.message.reply_text(
            "📖 VEXIS-CLI AI Agent Help\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/reset - Clear conversation history\n"
            "/help - Show this help message\n\n"
            "Just send any instruction and I'll execute it on your computer!"
        )
    
    async def _cancel_user_task(self, user_id: int):
        """Cancel any running task for the specified user"""
        async with self._task_lock:
            if user_id in self._current_tasks:
                task = self._current_tasks[user_id]
                if not task.done():
                    self.logger.info(f"Cancelling running task for user {user_id}")
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=2.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                del self._current_tasks[user_id]
    
    async def _process_message_async(self, user_message: str, user_id: int, 
                                      processing_msg, history) -> str:
        """Process message asynchronously with cancellation support"""
        if self.message_callback:
            # Run callback in thread pool to allow cancellation
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.message_callback, user_message, user_id)
        return "⚠️ Message callback not set. Bot not properly configured."
    
    @retry_on_network_error(max_retries=10, initial_delay=1.0, backoff_factor=2.0)
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages - cancels previous task and starts new one immediately"""
        user_id = update.effective_user.id
        
        if not self._is_user_allowed(user_id):
            await update.message.reply_text("Sorry, you are not authorized to use this bot.")
            return
        
        if not update.message or not update.message.text:
            return
        
        user_message = update.message.text
        
        # Check for /reset command
        if user_message.strip() == "/reset":
            await self.reset_command(update, context)
            return
        
        # Cancel any running task for this user immediately
        await self._cancel_user_task(user_id)
        
        # Add user message to conversation history
        history = self.get_conversation_history(user_id)
        history.add_message("user", user_message)
        
        # Send processing message
        processing_msg = await update.message.reply_text("⏳ Processing your request...")
        
        # Create and track new task for this user
        async with self._task_lock:
            task = asyncio.create_task(
                self._handle_message_task(user_id, user_message, processing_msg, history)
            )
            self._current_tasks[user_id] = task
    
    async def _handle_message_task(self, user_id: int, user_message: str, 
                                    processing_msg, history):
        """Actual message processing task that can be cancelled"""
        try:
            if self.message_callback:
                response = await self._process_message_async(user_message, user_id, processing_msg, history)
                
                # Check if task was cancelled
                if asyncio.current_task().cancelled():
                    self.logger.info(f"Task for user {user_id} was cancelled, skipping response")
                    return
                
                # Add assistant response to conversation history
                history.add_message("assistant", response)
                
                # Truncate long messages if exceeds Telegram limit (4096 chars)
                if len(response) > 4000:
                    self.logger.info(f"Response is {len(response)} chars, truncating with [omitted]")
                    response = self._truncate_message(response, max_length=4000)
                
                # Update processing message with response
                await processing_msg.edit_text(response)
                
                # Process any queued messages (e.g., Phase 2 summaries)
                await self.process_message_queue()
            else:
                await processing_msg.edit_text("⚠️ Message callback not set. Bot not properly configured.")
                
        except asyncio.CancelledError:
            self.logger.info(f"Task for user {user_id} cancelled - switching to new task")
            try:
                await processing_msg.edit_text("🔄 Task cancelled - processing new request...")
            except:
                pass
            raise
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            try:
                await processing_msg.edit_text(f"❌ Error processing your request: {str(e)}")
            except:
                pass
        finally:
            # Clean up task reference
            async with self._task_lock:
                if user_id in self._current_tasks and self._current_tasks[user_id] == asyncio.current_task():
                    del self._current_tasks[user_id]
    
    def _is_user_allowed(self, user_id: int) -> bool:
        """Check if user is allowed to use the bot"""
        if not self.allowed_user_ids:
            # If no allowed users specified, allow everyone
            return True
        return user_id in self.allowed_user_ids
    
    def _truncate_message(self, message: str, max_length: int = 4000) -> str:
        """Truncate message if it exceeds max length, adding [omitted] in the middle.
        
        Keeps beginning and end of message, omitting the middle portion.
        Format: "<beginning> [omitted] <end>"
        """
        if len(message) <= max_length:
            return message
        
        omitted_tag = " [omitted] "
        available_space = max_length - len(omitted_tag)
        half_space = available_space // 2
        
        beginning = message[:half_space]
        end = message[-half_space:]
        
        return f"{beginning}{omitted_tag}{end}"
    
    @retry_on_network_error(max_retries=10, initial_delay=1.0, backoff_factor=2.0)
    async def send_message(self, chat_id: int, message: str):
        """Send a message to a specific chat"""
        if not self.application:
            self.logger.error("Telegram application not initialized")
            return False
        
        # Truncate if too long
        if len(message) > 4000:
            self.logger.warning(f"Message too long ({len(message)} chars), truncating with [omitted]")
            message = self._truncate_message(message, max_length=4000)
        
        await self.application.bot.send_message(chat_id=chat_id, text=message)
        return True
    
    def queue_message(self, chat_id: int, message: str):
        """
        Queue a message to be sent from the async event loop.
        This method is synchronous and can be called from any context.
        """
        self.message_queue.append((chat_id, message))
        self.logger.info(f"Message queued for user {chat_id}")
    
    async def process_message_queue(self):
        """Process queued messages and send them"""
        while self.message_queue:
            chat_id, message = self.message_queue.pop(0)
            try:
                await self.send_message(chat_id, message)
                self.logger.info(f"Sent queued message to user {chat_id}")
            except Exception as e:
                self.logger.error(f"Failed to send queued message to user {chat_id}: {e}")
                # Re-queue the message for retry (at the end of the queue)
                self.message_queue.append((chat_id, message))
                # Add a small delay before retrying to avoid tight loop
                await asyncio.sleep(1)
    
    def _start_queue_processor(self):
        """Start background thread to process message queue"""
        def queue_processor():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.queue_processor_running = True
            
            while self.queue_processor_running:
                if self.message_queue and self.application:
                    try:
                        # Copy the queue to avoid concurrent modification
                        messages_to_send = list(self.message_queue)
                        self.message_queue.clear()
                        
                        for chat_id, message in messages_to_send:
                            try:
                                loop.run_until_complete(self.send_message(chat_id, message))
                                self.logger.info(f"Sent queued message to user {chat_id}")
                            except Exception as e:
                                self.logger.error(f"Failed to send queued message to user {chat_id}: {e}")
                                # Re-queue the message for retry
                                self.message_queue.append((chat_id, message))
                                # Add delay before continuing to next message
                                time.sleep(1)
                    except Exception as e:
                        self.logger.error(f"Error in queue processor: {e}")
                        # Add delay before retrying the entire batch
                        time.sleep(2)
                
                # Sleep briefly to avoid busy-waiting
                time.sleep(0.1)
            
            loop.close()
            self.logger.info("Queue processor stopped")
        
        self.queue_processor_thread = threading.Thread(target=queue_processor, daemon=True)
        self.queue_processor_thread.start()
        self.logger.info("Queue processor thread started")
    
    def _stop_queue_processor(self):
        """Stop background queue processor"""
        self.queue_processor_running = False
        if self.queue_processor_thread:
            self.queue_processor_thread.join(timeout=2)
            self.logger.info("Queue processor thread stopped")
    
    def start_bot(self):
        """Start the Telegram bot (blocking)"""
        if not TELEGRAM_AVAILABLE:
            self.logger.error("Cannot start bot: python-telegram-bot not installed")
            return False

        if not self.bot_token:
            self.logger.error("Cannot start bot: bot_token not set")
            return False

        # Outer loop to ensure session remains active after task completion
        while True:
            try:
                # Create application
                self.application = Application.builder().token(self.bot_token).build()

                # Add handlers
                self.application.add_handler(CommandHandler("start", self.start_command))
                self.application.add_handler(CommandHandler("reset", self.reset_command))
                self.application.add_handler(CommandHandler("help", self.help_command))
                self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

                # Start queue processor thread
                self._start_queue_processor()

                # Start bot
                self.is_running = True
                self.logger.info("Starting Telegram bot...")
                self.application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

                # After run_polling returns (e.g., due to network error),
                # the loop will restart and wait for the next task
                self.logger.info("Telegram bot polling stopped, restarting to wait for next task...")
                self.is_running = False
                self._stop_queue_processor()

                # Small delay before restarting to avoid rapid restart loops
                time.sleep(2)

            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received, stopping Telegram bot")
                self.is_running = False
                self._stop_queue_processor()
                break
            except Exception as e:
                self.logger.error(f"Error in Telegram bot: {e}")
                self.is_running = False
                self._stop_queue_processor()
                # Wait before restarting to avoid rapid error loops
                self.logger.info("Waiting 5 seconds before restarting...")
                time.sleep(5)

        return True
    
    def stop_bot(self):
        """Stop the Telegram bot"""
        self.is_running = False
        self._stop_queue_processor()
        
        if self.application:
            self.logger.info("Stopping Telegram bot...")
            # Note: Proper shutdown requires async context, this is a simplified version
            if self.application.running:
                asyncio.create_task(self.application.shutdown())


def create_telegram_bot(config_path: Optional[str] = None) -> Optional[TelegramBotManager]:
    """
    Create a Telegram bot manager from configuration
    
    Args:
        config_path: Path to config.yaml file. If None, loads from default location.
        
    Returns:
        TelegramBotManager instance or None if telegram is disabled or not available
    """
    if not TELEGRAM_AVAILABLE:
        print("⚠️ python-telegram-bot library not installed")
        print("To enable Telegram mode, install it with:")
        print("  pip install python-telegram-bot>=21.0.0")
        return None
    
    try:
        import yaml
        from pathlib import Path
        
        # Load config directly from YAML to avoid singleton cache
        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                config_dict = yaml.safe_load(f)
        else:
            # Fallback to default config loading
            config_obj = load_config()
            config_dict = config_obj.__dict__ if hasattr(config_obj, '__dict__') else {}
        
        # Extract telegram config
        telegram_dict = config_dict.get('telegram', {})
        
        if not telegram_dict.get('enabled', False):
            return None
        
        bot_token = telegram_dict.get('bot_token', '')
        if not bot_token:
            print("⚠️ Telegram bot token not configured")
            print("Please set bot_token in config.yaml under telegram section")
            return None
        
        allowed_user_ids = telegram_dict.get('allowed_user_ids', [])
        max_history_length = telegram_dict.get('max_history_length', 50)
        
        return TelegramBotManager(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            max_history_length=max_history_length
        )
    except Exception as e:
        print(f"⚠️ Error loading Telegram configuration: {e}")
        return None