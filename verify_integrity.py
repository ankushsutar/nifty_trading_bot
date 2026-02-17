import os
import sys
import compileall
import importlib.util

def check_syntax(directory):
    print(f"--- Checking Syntax in {directory} ---")
    try:
        # force=True to recompile, quiet=1 to only print errors
        if compileall.compile_dir(directory, force=True, quiet=1):
            print("✅ Syntax Check Passed")
            return True
        else:
            print("❌ Syntax Check Failed")
            return False
    except Exception as e:
        print(f"❌ Error running compileall: {e}")
        return False

def check_imports():
    print("\n--- Checking Critical Imports ---")
    modules = [
        "bot.main",
        "bot.core.position_manager",
        "bot.strategies.momentum_strategy",
        "bot.strategies.orb_strategy",
        "bot.strategies.vwap_strategy",
        "bot.strategies.nifty_straddle",
        "bot.strategies.inside_bar_strategy",
        "bot.strategies.ohl_strategy",
        "bot.utils.rate_limiter",
        "bot.config.settings"
    ]
    
    all_passed = True
    for mod_name in modules:
        try:
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            importlib.import_module(mod_name)
            print(f"✅ Import Successful: {mod_name}")
        except Exception as e:
            print(f"❌ Import Failed: {mod_name} -> {e}")
            all_passed = False
    return all_passed

if __name__ == "__main__":
    current_dir = os.getcwd()
    print(f"Scanning directory: {current_dir}")
    
    syntax_ok = check_syntax(os.path.join(current_dir, "bot"))
    imports_ok = check_imports()
    
    if syntax_ok and imports_ok:
        print("\n🎉 INTEGRITY CHECK PASSED: Codebase is syntactically correct and importable.")
        sys.exit(0)
    else:
        print("\n🚫 INTEGRITY CHECK FAILED: Fix errors above.")
        sys.exit(1)
