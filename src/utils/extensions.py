"""Extension system for AirLLMEasy to allow dynamically added plugins and tools."""
import os
import sys
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable


class BaseExtension:
    """Base class that all AirLLMEasy extensions must inherit from."""
    name = "Unknown Extension"
    description = "No description provided."
    version = "1.0"
    author = "Unknown"

    def __init__(self):
        self.app_context = None

    def on_load(self, app_context) -> None:
        """Called when the extension is loaded. `app_context` is usually the MainWindow."""
        self.app_context = app_context

    def on_unload(self) -> None:
        """Called when the extension is disabled or application is closing."""
        pass

    def get_ai_tools(self) -> List[Dict[str, Any]]:
        """
        Return a list of AI tools the extension provides.
        Format per tool: 
        {
            "name": "my_tool",
            "description": "Does something cool. Required args: x, y",
            "format": '{"tool": "my_tool", "arg1": "value"}',
            "handler": self.my_tool_handler  # Callable taking a dict of arguments and returning a string result
        }
        """
        return []


class ExtensionManager:
    """Discovers and manages loaded extensions."""
    
    def __init__(self, config):
        self.config = config
        self.extensions: Dict[str, BaseExtension] = {}
        
        # Determine the correct base path depending on PyInstaller
        if getattr(sys, 'frozen', False):
            base_path = Path(sys.executable).parent
        else:
            base_path = Path(__file__).resolve().parent.parent.parent

        self.extensions_dir = (base_path / "extensions").resolve()
        self.extensions_dir.mkdir(parents=True, exist_ok=True)

    def load_all(self, app_context) -> None:
        """Loads all Python files in the extensions folder."""
        for ext_file in self.extensions_dir.glob("*.py"):
            if ext_file.name.startswith("__"):
                continue
            
            try:
                module_name = f"airllm_extension_{ext_file.stem}"
                spec = importlib.util.spec_from_file_location(module_name, str(ext_file))
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    
                    if hasattr(module, "get_extension"):
                        ext_instance = module.get_extension()
                        if isinstance(ext_instance, BaseExtension):
                            ext_instance.on_load(app_context)
                            self.extensions[ext_instance.name] = ext_instance
                            print(f"[ExtensionManager] Loaded: {ext_instance.name}")
            except Exception as e:
                print(f"[ExtensionManager] Failed to load {ext_file.name}: {e}")

    def get_all_tools(self) -> List[Dict[str, Any]]:
        """Gather all tools from all loaded extensions."""
        tools = []
        for ext in self.extensions.values():
            try:
                ext_tools = ext.get_ai_tools()
                for tool_def in ext_tools:
                    # Validate
                    if "name" in tool_def and "handler" in tool_def:
                        tools.append(tool_def)
            except Exception as e:
                print(f"[ExtensionManager] Error getting tools for {ext.name}: {e}")
        return tools
