"""
RAM Manager for VEXIS-CLI AI Agent System
Monitors system RAM usage and enforces memory limits to prevent system crashes
"""

import os
import time
import threading
from typing import Optional, Dict, Any, Callable, Tuple, List
from dataclasses import dataclass, field
from enum import Enum

try:
    import psutil
except ImportError:
    psutil = None

from .logger import get_logger


class RamStatus(Enum):
    """RAM usage status levels"""
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    LIMIT_EXCEEDED = "limit_exceeded"


@dataclass
class RamInfo:
    """RAM usage information"""
    total_mb: float
    used_mb: float
    available_mb: float
    usage_percentage: float
    status: RamStatus
    timestamp: float = field(default_factory=time.time)


@dataclass
class RamConfig:
    """Configuration for RAM management"""
    max_usage_percentage: float = 70.0  # 70% threshold
    resume_percentage: float = 35.0     # 35% threshold for resume
    check_interval: float = 1.0         # Check every second
    enable_auto_pause: bool = True
    enable_command_limits: bool = True


class RamManager:
    """
    Manages RAM usage monitoring and enforcement for the AI agent system
    """
    
    def __init__(self, config: Optional[RamConfig] = None):
        self.config = config or RamConfig()
        self.logger = get_logger("ram_manager")
        
        # State tracking
        self.is_paused = False
        self.pause_callbacks: List[Callable[[], None]] = []
        self.resume_callbacks: List[Callable[[], None]] = []
        
        # Monitoring thread
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # RAM info cache
        self._current_ram_info: Optional[RamInfo] = None
        self._last_check_time = 0.0
        
        self.logger.info("RAM Manager initialized",
                        max_usage=self.config.max_usage_percentage,
                        resume_threshold=self.config.resume_percentage,
                        check_interval=self.config.check_interval)
    
    def get_ram_info(self) -> RamInfo:
        """Get current RAM usage information"""
        if psutil is None:
            self.logger.warning("psutil not available - RAM monitoring disabled")
            return RamInfo(
                total_mb=0.0,
                used_mb=0.0,
                available_mb=0.0,
                usage_percentage=0.0,
                status=RamStatus.NORMAL
            )
        
        try:
            # Get system memory info
            memory = psutil.virtual_memory()
            
            total_mb = memory.total / (1024 * 1024)
            used_mb = memory.used / (1024 * 1024)
            available_mb = memory.available / (1024 * 1024)
            usage_percentage = memory.percent
            
            # Determine status
            if usage_percentage >= self.config.max_usage_percentage:
                status = RamStatus.LIMIT_EXCEEDED
            elif usage_percentage >= self.config.max_usage_percentage - 5:
                status = RamStatus.CRITICAL
            elif usage_percentage >= self.config.max_usage_percentage - 15:
                status = RamStatus.WARNING
            else:
                status = RamStatus.NORMAL
            
            ram_info = RamInfo(
                total_mb=total_mb,
                used_mb=used_mb,
                available_mb=available_mb,
                usage_percentage=usage_percentage,
                status=status
            )
            
            self._current_ram_info = ram_info
            self._last_check_time = time.time()
            
            return ram_info
            
        except Exception as e:
            self.logger.error(f"Failed to get RAM info: {e}")
            # Return default info on error
            return RamInfo(
                total_mb=0.0,
                used_mb=0.0,
                available_mb=0.0,
                usage_percentage=0.0,
                status=RamStatus.NORMAL
            )
    
    def check_ram_limit(self) -> bool:
        """Check if RAM usage exceeds the limit"""
        ram_info = self.get_ram_info()
        return ram_info.usage_percentage >= self.config.max_usage_percentage
    
    def check_can_resume(self) -> bool:
        """Check if RAM usage is low enough to resume"""
        ram_info = self.get_ram_info()
        return ram_info.usage_percentage <= self.config.resume_percentage
    
    def can_execute_command(self) -> Tuple[bool, str]:
        """
        Check if a command can be executed based on RAM usage
        
        Returns:
            Tuple of (can_execute, reason)
        """
        if not self.config.enable_command_limits:
            return True, "Command limits disabled"
        
        ram_info = self.get_ram_info()
        
        if ram_info.usage_percentage >= self.config.max_usage_percentage:
            reason = f"Cannot execute because the RAM limit has been reached ({ram_info.usage_percentage:.1f}% >= {self.config.max_usage_percentage}%)"
            self.logger.warning("Command execution blocked due to RAM limit", 
                              usage_percentage=ram_info.usage_percentage,
                              limit=self.config.max_usage_percentage)
            return False, reason
        
        if ram_info.status == RamStatus.CRITICAL:
            reason = f"RAM usage is critical ({ram_info.usage_percentage:.1f}%), command execution not recommended"
            self.logger.warning("Command execution not recommended due to critical RAM usage",
                              usage_percentage=ram_info.usage_percentage)
            return True, reason  # Still allow but warn
        
        return True, "RAM usage is within acceptable limits"
    
    def add_pause_callback(self, callback: Callable[[], None]):
        """Add callback to be called when system is paused"""
        self.pause_callbacks.append(callback)
    
    def add_resume_callback(self, callback: Callable[[], None]):
        """Add callback to be called when system is resumed"""
        self.resume_callbacks.append(callback)
    
    def pause_system(self, reason: str = "RAM limit exceeded"):
        """Pause the system due to RAM constraints"""
        if self.is_paused:
            return
        
        self.is_paused = True
        self.logger.warning("System paused due to RAM constraints", reason=reason)
        
        # Call pause callbacks
        for callback in self.pause_callbacks:
            try:
                callback()
            except Exception as e:
                self.logger.error(f"Error in pause callback: {e}")
    
    def resume_system(self, reason: str = "RAM usage normalized"):
        """Resume the system when RAM constraints are resolved"""
        if not self.is_paused:
            return
        
        self.is_paused = False
        self.logger.info("System resumed", reason=reason)
        
        # Call resume callbacks
        for callback in self.resume_callbacks:
            try:
                callback()
            except Exception as e:
                self.logger.error(f"Error in resume callback: {e}")
    
    def start_monitoring(self):
        """Start background RAM monitoring"""
        if self._monitoring:
            return
        
        self._monitoring = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        
        self.logger.info("RAM monitoring started")
    
    def stop_monitoring(self):
        """Stop background RAM monitoring"""
        if not self._monitoring:
            return
        
        self._monitoring = False
        self._stop_event.set()
        
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)
        
        self.logger.info("RAM monitoring stopped")
    
    def _monitor_loop(self):
        """Background monitoring loop"""
        while not self._stop_event.is_set():
            try:
                ram_info = self.get_ram_info()
                
                # Log status changes
                if self._current_ram_info and ram_info.status != self._current_ram_info.status:
                    self.logger.info("RAM status changed", 
                                    old_status=self._current_ram_info.status.value,
                                    new_status=ram_info.status.value,
                                    usage_percentage=ram_info.usage_percentage)
                
                # Check for pause/resume conditions
                if self.config.enable_auto_pause:
                    if not self.is_paused and ram_info.usage_percentage >= self.config.max_usage_percentage:
                        self.pause_system(f"RAM usage exceeded limit: {ram_info.usage_percentage:.1f}%")
                    elif self.is_paused and ram_info.usage_percentage <= self.config.resume_percentage:
                        self.resume_system(f"RAM usage normalized: {ram_info.usage_percentage:.1f}%")
                
                # Sleep for the configured interval
                self._stop_event.wait(self.config.check_interval)
                
            except Exception as e:
                self.logger.error(f"Error in RAM monitoring loop: {e}")
                self._stop_event.wait(self.config.check_interval)
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get a summary of current RAM management status"""
        ram_info = self.get_ram_info()
        
        return {
            "ram_usage": {
                "total_mb": round(ram_info.total_mb, 2),
                "used_mb": round(ram_info.used_mb, 2),
                "available_mb": round(ram_info.available_mb, 2),
                "usage_percentage": round(ram_info.usage_percentage, 2),
                "status": ram_info.status.value
            },
            "system_status": {
                "is_paused": self.is_paused,
                "monitoring_active": self._monitoring,
                "auto_pause_enabled": self.config.enable_auto_pause,
                "command_limits_enabled": self.config.enable_command_limits
            },
            "thresholds": {
                "max_usage_percentage": self.config.max_usage_percentage,
                "resume_percentage": self.config.resume_percentage,
                "check_interval": self.config.check_interval
            }
        }


# Global RAM manager instance
_ram_manager: Optional[RamManager] = None


def get_ram_manager(config: Optional[RamConfig] = None) -> RamManager:
    """Get the global RAM manager instance"""
    global _ram_manager
    if _ram_manager is None:
        _ram_manager = RamManager(config)
    return _ram_manager


def initialize_ram_manager(config: Optional[RamConfig] = None) -> RamManager:
    """Initialize the global RAM manager"""
    global _ram_manager
    _ram_manager = RamManager(config)
    return _ram_manager
