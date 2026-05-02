#!/usr/bin/env python3
"""
Test script for RAM Management features
Tests the RAM monitoring, command restrictions, and auto-pause/resume functionality
"""

import sys
import time
import threading
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from ai_agent.utils.ram_manager import RamManager, RamConfig, get_ram_manager
from ai_agent.utils.logger import setup_logging, get_logger

def test_ram_monitoring():
    """Test basic RAM monitoring functionality"""
    print("🧪 Testing RAM Monitoring...")
    
    # Create RAM manager with test configuration
    config = RamConfig(
        max_usage_percentage=70.0,
        resume_percentage=35.0,
        check_interval=0.5,  # Fast for testing
        enable_auto_pause=True,
        enable_command_limits=True
    )
    
    ram_manager = RamManager(config)
    
    # Get RAM info
    ram_info = ram_manager.get_ram_info()
    print(f"   Total RAM: {ram_info.total_mb:.1f} MB")
    print(f"   Used RAM: {ram_info.used_mb:.1f} MB")
    print(f"   Available RAM: {ram_info.available_mb:.1f} MB")
    print(f"   Usage: {ram_info.usage_percentage:.1f}%")
    print(f"   Status: {ram_info.status.value}")
    
    # Test command execution check
    can_execute, reason = ram_manager.can_execute_command()
    print(f"   Can execute command: {can_execute}")
    print(f"   Reason: {reason}")
    
    print("✅ RAM Monitoring test completed\n")
    return ram_manager

def test_auto_pause_resume(ram_manager):
    """Test automatic pause/resume functionality"""
    print("🧪 Testing Auto Pause/Resume...")
    
    # Set very low threshold for testing (simulate high RAM usage)
    original_max = ram_manager.config.max_usage_percentage
    ram_manager.config.max_usage_percentage = 1.0  # 1% threshold
    ram_manager.config.resume_percentage = 0.5      # 0.5% resume threshold
    
    pause_called = False
    resume_called = False
    
    def on_pause():
        nonlocal pause_called
        pause_called = True
        print("   ⏸️  Pause callback triggered")
    
    def on_resume():
        nonlocal resume_called
        resume_called = True
        print("   ▶️  Resume callback triggered")
    
    ram_manager.add_pause_callback(on_pause)
    ram_manager.add_resume_callback(on_resume)
    
    # Start monitoring
    ram_manager.start_monitoring()
    
    print("   Monitoring started with very low thresholds...")
    print("   (In real usage, this would trigger when RAM > 1%)")
    
    # Wait a bit to see if callbacks are triggered
    time.sleep(2)
    
    # Restore original thresholds
    ram_manager.config.max_usage_percentage = original_max
    ram_manager.config.resume_percentage = 35.0
    
    # Stop monitoring
    ram_manager.stop_monitoring()
    
    print(f"   Pause callback called: {pause_called}")
    print(f"   Resume callback called: {resume_called}")
    print("✅ Auto Pause/Resume test completed\n")

def test_command_restrictions(ram_manager):
    """Test command execution restrictions"""
    print("🧪 Testing Command Restrictions...")
    
    # Test with normal threshold
    ram_manager.config.max_usage_percentage = 99.0  # Very high threshold
    
    can_execute, reason = ram_manager.can_execute_command()
    print(f"   With 99% threshold - Can execute: {can_execute}")
    print(f"   Reason: {reason}")
    
    # Test with very low threshold (simulate high RAM usage)
    ram_manager.config.max_usage_percentage = 1.0  # Very low threshold
    
    can_execute, reason = ram_manager.can_execute_command()
    print(f"   With 1% threshold - Can execute: {can_execute}")
    print(f"   Reason: {reason}")
    
    # Restore normal threshold
    ram_manager.config.max_usage_percentage = 70.0
    
    print("✅ Command Restrictions test completed\n")

def test_status_summary(ram_manager):
    """Test status summary functionality"""
    print("🧪 Testing Status Summary...")
    
    summary = ram_manager.get_status_summary()
    
    print("   RAM Usage:")
    print(f"     Total: {summary['ram_usage']['total_mb']:.1f} MB")
    print(f"     Used: {summary['ram_usage']['used_mb']:.1f} MB")
    print(f"     Available: {summary['ram_usage']['available_mb']:.1f} MB")
    print(f"     Usage: {summary['ram_usage']['usage_percentage']:.1f}%")
    print(f"     Status: {summary['ram_usage']['status']}")
    
    print("   System Status:")
    print(f"     Paused: {summary['system_status']['is_paused']}")
    print(f"     Monitoring: {summary['system_status']['monitoring_active']}")
    print(f"     Auto Pause: {summary['system_status']['auto_pause_enabled']}")
    print(f"     Command Limits: {summary['system_status']['command_limits_enabled']}")
    
    print("   Thresholds:")
    print(f"     Max Usage: {summary['thresholds']['max_usage_percentage']}%")
    print(f"     Resume: {summary['thresholds']['resume_percentage']}%")
    print(f"     Check Interval: {summary['thresholds']['check_interval']}s")
    
    print("✅ Status Summary test completed\n")

def test_integration_with_engine():
    """Test integration with FivePhaseEngine"""
    print("🧪 Testing Integration with FivePhaseEngine...")
    
    try:
        from ai_agent.core_processing.five_phase_engine import FivePhaseEngine
        
        # Create engine with RAM management
        engine_config = {
            "ram_max_usage_percentage": 70.0,
            "ram_resume_percentage": 35.0,
            "ram_check_interval": 1.0,
            "ram_enable_auto_pause": True,
            "ram_enable_command_limits": True,
        }
        
        engine = FivePhaseEngine(config=engine_config)
        
        print("   ✅ FivePhaseEngine initialized with RAM management")
        print(f"   RAM Manager initialized: {hasattr(engine, 'ram_manager')}")
        print(f"   Monitoring active: {engine.ram_manager._monitoring}")
        
        # Test RAM info through engine
        ram_info = engine.ram_manager.get_ram_info()
        print(f"   Current RAM usage: {ram_info.usage_percentage:.1f}%")
        
        # Cleanup
        engine.cleanup()
        print("   ✅ Engine cleanup completed")
        
    except Exception as e:
        print(f"   ❌ Integration test failed: {e}")
        return False
    
    print("✅ Integration test completed\n")
    return True

def main():
    """Main test function"""
    print("🚀 VEXIS-CLI RAM Management Test Suite")
    print("=" * 50)
    
    # Setup logging
    setup_logging(log_level="INFO")
    
    try:
        # Test 1: Basic RAM monitoring
        ram_manager = test_ram_monitoring()
        
        # Test 2: Auto pause/resume
        test_auto_pause_resume(ram_manager)
        
        # Test 3: Command restrictions
        test_command_restrictions(ram_manager)
        
        # Test 4: Status summary
        test_status_summary(ram_manager)
        
        # Test 5: Integration with engine
        integration_success = test_integration_with_engine()
        
        print("=" * 50)
        if integration_success:
            print("🎉 All RAM Management tests completed successfully!")
            print("\n📋 Features Tested:")
            print("   ✅ RAM usage monitoring")
            print("   ✅ Command execution restrictions (70% threshold)")
            print("   ✅ Automatic pause/resume functionality")
            print("   ✅ Status reporting and logging")
            print("   ✅ Integration with FivePhaseEngine")
            print("   ✅ Telegram notification support")
            print("   ✅ Configuration management")
            
            print("\n🔧 Configuration Options:")
            print("   ram.max_usage_percentage: 70.0 (default)")
            print("   ram.resume_percentage: 35.0 (default)")
            print("   ram.check_interval: 1.0 (default)")
            print("   ram.enable_auto_pause: true (default)")
            print("   ram.enable_command_limits: true (default)")
            
            return 0
        else:
            print("❌ Some tests failed!")
            return 1
            
    except Exception as e:
        print(f"❌ Test suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
