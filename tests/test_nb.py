import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock

# Add parent directory to path so we can import nb
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import nb


class TestConfig(unittest.TestCase):
    def test_config_from_dict_valid(self):
        config_dict = {
            "notebooks_path": "~/notebooks",
            "jupyter_path": "/usr/bin/jupyter",
            "ipython_path": "/usr/bin/ipython"
        }
        config = nb.Config.from_dict(config_dict)
        self.assertEqual(config.notebooks_path, os.path.expanduser("~/notebooks"))
        self.assertEqual(config.jupyter_path, "/usr/bin/jupyter")
        self.assertEqual(config.ipython_path, "/usr/bin/ipython")
        # Check default values
        self.assertEqual(config.cache_path, os.path.expanduser("~/.nb/cache/"))

    def test_config_from_dict_missing_keys(self):
        config_dict = {
            "jupyter_path": "/usr/bin/jupyter",
            # Missing notebooks_path and ipython_path
        }
        with self.assertRaises(ValueError):
            nb.Config.from_dict(config_dict)


class TestLoadConfig(unittest.TestCase):
    def test_load_config(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
            temp_file.write("""[default]
notebooks_path = "~/test_notebooks"
jupyter_path = "/usr/local/bin/jupyter"
ipython_path = "/usr/local/bin/ipython"
""")

        try:
            config = nb.load_config(temp_file.name)
            self.assertEqual(config.notebooks_path, os.path.expanduser("~/test_notebooks"))
            self.assertEqual(config.jupyter_path, "/usr/local/bin/jupyter")
            self.assertEqual(config.ipython_path, "/usr/local/bin/ipython")
        finally:
            os.unlink(temp_file.name)

    def test_load_config_invalid(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as temp_file:
            temp_file.write("""[default]
# Missing required fields
""")

        try:
            with self.assertRaises(SystemExit):
                nb.load_config(temp_file.name)
        finally:
            os.unlink(temp_file.name)


class TestLockFile(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.lock_path = os.path.join(self.temp_dir, "test.lock")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @mock.patch('fcntl.flock')
    def test_lock_file(self, mock_flock):
        with nb.lock_file(self.lock_path):
            mock_flock.assert_any_call(mock.ANY, nb.fcntl.LOCK_EX)
            
        # Check if unlock was called
        mock_flock.assert_any_call(mock.ANY, nb.fcntl.LOCK_UN)
        
        # Check if directory was created
        self.assertTrue(os.path.exists(os.path.dirname(self.lock_path)))


class TestInterpreterPaths(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config = nb.Config(
            notebooks_path=os.path.join(self.temp_dir, "notebooks"),
            jupyter_path="/usr/bin/jupyter",
            ipython_path="/usr/bin/ipython",
            cache_path=os.path.join(self.temp_dir, "cache"),
            lock_file_path=os.path.join(self.temp_dir, "lock"),
            interpreters_mapping_path=os.path.join(self.temp_dir, "interpreters.json")
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_get_interpreter_path_default(self):
        # When mapping doesn't exist, should return default ipython_path
        path = nb.get_interpreter_path(self.config, "test_notebook")
        self.assertEqual(path, self.config.ipython_path)

    def test_get_interpreter_path_from_mapping(self):
        # Create mapping file
        os.makedirs(os.path.dirname(self.config.interpreters_mapping_path), exist_ok=True)
        mapping = {"test_notebook": "/custom/ipython"}
        with open(self.config.interpreters_mapping_path, "w") as f:
            json.dump(mapping, f)

        path = nb.get_interpreter_path(self.config, "test_notebook")
        self.assertEqual(path, "/custom/ipython")

    def test_set_interpreter_path_new(self):
        nb.set_interpreter_path(self.config, "test_notebook", "/new/ipython")
        
        # Check if file was created
        self.assertTrue(os.path.exists(self.config.interpreters_mapping_path))
        
        # Check content
        with open(self.config.interpreters_mapping_path, "r") as f:
            mapping = json.load(f)
            self.assertEqual(mapping["test_notebook"], "/new/ipython")

    def test_set_interpreter_path_update(self):
        # Create initial mapping
        os.makedirs(os.path.dirname(self.config.interpreters_mapping_path), exist_ok=True)
        mapping = {"test_notebook": "/old/ipython", "other_notebook": "/other/ipython"}
        with open(self.config.interpreters_mapping_path, "w") as f:
            json.dump(mapping, f)

        nb.set_interpreter_path(self.config, "test_notebook", "/updated/ipython")
        
        # Check content
        with open(self.config.interpreters_mapping_path, "r") as f:
            updated_mapping = json.load(f)
            self.assertEqual(updated_mapping["test_notebook"], "/updated/ipython")
            self.assertEqual(updated_mapping["other_notebook"], "/other/ipython")


class TestTransformNotebook(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config = nb.Config(
            notebooks_path=os.path.join(self.temp_dir, "notebooks"),
            jupyter_path="jupyter",  # Using just the name as we'll mock the os.system call
            ipython_path="/usr/bin/ipython",
            cache_path=os.path.join(self.temp_dir, "cache"),
            lock_file_path=os.path.join(self.temp_dir, "lock"),
            interpreters_mapping_path=os.path.join(self.temp_dir, "interpreters.json")
        )
        os.makedirs(self.config.notebooks_path, exist_ok=True)
        os.makedirs(self.config.cache_path, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @mock.patch('os.system')
    @mock.patch('nb.set_interpreter_path')
    def test_transform_notebook_with_markers(self, mock_set_interpreter, mock_system):
        notebook_path = os.path.join(self.config.notebooks_path, "test_notebook.ipynb")
        script_path = os.path.join(self.config.cache_path, "test_notebook.py")

        # Mock os.system to create a fake converted .py file
        def fake_convert(cmd):
            temp_script_path = cmd.split("--output ")[1].split()[0] + ".py"
            with open(temp_script_path, "w") as f:
                f.write("""
# Some initial comments
print("This will be ignored")

# nb.start
print("This is inside the markers")
# ipython_path = "/custom/ipython"
print("More code inside markers")
# nb.end

print("This will be ignored too")
""")
            return 0

        mock_system.side_effect = fake_convert

        result = nb.transform_notebook(self.config, "test_notebook", notebook_path, script_path)
        
        self.assertTrue(result)
        self.assertTrue(os.path.exists(script_path))
        
        # Check content of the output script
        with open(script_path, "r") as f:
            content = f.read()
            self.assertIn("This is inside the markers", content)
            self.assertIn("More code inside markers", content)
            self.assertNotIn("This will be ignored", content)
            self.assertNotIn("This will be ignored too", content)
        
        # Verify that set_interpreter_path was called
        mock_set_interpreter.assert_called_once_with(self.config, "test_notebook", "/custom/ipython")

    @mock.patch('os.system')
    def test_transform_notebook_no_markers(self, mock_system):
        notebook_path = os.path.join(self.config.notebooks_path, "test_notebook.ipynb")
        script_path = os.path.join(self.config.cache_path, "test_notebook.py")

        # Mock os.system to create a fake converted .py file
        def fake_convert(cmd):
            temp_script_path = cmd.split("--output ")[1].split()[0] + ".py"
            with open(temp_script_path, "w") as f:
                f.write("""
# Some initial comments
print("This will be included")
print("All code is included as there are no markers")
""")
            return 0

        mock_system.side_effect = fake_convert

        result = nb.transform_notebook(self.config, "test_notebook", notebook_path, script_path)
        
        self.assertTrue(result)
        self.assertTrue(os.path.exists(script_path))
        
        # Check content of the output script
        with open(script_path, "r") as f:
            content = f.read()
            self.assertIn("This will be included", content)
            self.assertIn("All code is included as there are no markers", content)

    @mock.patch('os.system')
    def test_transform_notebook_invalid_markers(self, mock_system):
        notebook_path = os.path.join(self.config.notebooks_path, "test_notebook.ipynb")
        script_path = os.path.join(self.config.cache_path, "test_notebook.py")

        # Mock os.system to create a fake converted .py file with invalid marker usage
        def fake_convert(cmd):
            temp_script_path = cmd.split("--output ")[1].split()[0] + ".py"
            with open(temp_script_path, "w") as f:
                f.write("""
# nb.start
print("First marker")
# nb.start
print("Nested start marker - should raise error")
# nb.end
""")
            return 0

        mock_system.side_effect = fake_convert

        with self.assertRaises(ValueError):
            nb.transform_notebook(self.config, "test_notebook", notebook_path, script_path)


class TestCachePythonFiles(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.notebooks_path = os.path.join(self.temp_dir, "notebooks")
        self.cache_path = os.path.join(self.temp_dir, "cache")
        os.makedirs(self.notebooks_path, exist_ok=True)
        os.makedirs(self.cache_path, exist_ok=True)

        self.config = nb.Config(
            notebooks_path=self.notebooks_path,
            jupyter_path="/usr/bin/jupyter",
            ipython_path="/usr/bin/ipython",
            cache_path=self.cache_path,
            lock_file_path=os.path.join(self.temp_dir, "lock"),
            interpreters_mapping_path=os.path.join(self.temp_dir, "interpreters.json")
        )

        # Create some test files
        self.py_file_path = os.path.join(self.notebooks_path, "test.py")
        self.ipynb_file_path = os.path.join(self.notebooks_path, "test.ipynb")
        self.txt_file_path = os.path.join(self.notebooks_path, "test.txt")
        
        with open(self.py_file_path, "w") as f:
            f.write("print('hello')")
        with open(self.ipynb_file_path, "w") as f:
            f.write("{}")  # Empty notebook
        with open(self.txt_file_path, "w") as f:
            f.write("text file")

        # Create subdirectory with files
        self.sub_dir = os.path.join(self.notebooks_path, "subdir")
        os.makedirs(self.sub_dir, exist_ok=True)
        self.sub_py_file = os.path.join(self.sub_dir, "sub.py")
        with open(self.sub_py_file, "w") as f:
            f.write("print('subdir')")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_cache_python_files(self):
        nb.cache_python_files(self.config)
        
        # Check that Python and notebook files were cached
        self.assertTrue(os.path.exists(os.path.join(self.cache_path, "test.py")))
        self.assertTrue(os.path.exists(os.path.join(self.cache_path, "test.ipynb")))
        self.assertTrue(os.path.exists(os.path.join(self.cache_path, "subdir", "sub.py")))
        
        # Text file should not be cached
        self.assertFalse(os.path.exists(os.path.join(self.cache_path, "test.txt")))


class TestBuildNotebook(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.notebooks_path = os.path.join(self.temp_dir, "notebooks")
        self.cache_path = os.path.join(self.temp_dir, "cache")
        os.makedirs(self.notebooks_path, exist_ok=True)
        os.makedirs(self.cache_path, exist_ok=True)

        self.config = nb.Config(
            notebooks_path=self.notebooks_path,
            jupyter_path="/usr/bin/jupyter",
            ipython_path="/usr/bin/ipython",
            cache_path=self.cache_path,
            lock_file_path=os.path.join(self.temp_dir, "lock"),
            interpreters_mapping_path=os.path.join(self.temp_dir, "interpreters.json")
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @mock.patch('nb.transform_notebook')
    @mock.patch('nb.cache_python_files')
    def test_build_notebook_existing(self, mock_cache, mock_transform):
        # Create notebook file
        notebook_path = os.path.join(self.notebooks_path, "test.ipynb")
        with open(notebook_path, "w") as f:
            f.write("{}")  # Empty notebook
        
        # Create cached script
        script_path = os.path.join(self.cache_path, "test.py")
        with open(script_path, "w") as f:
            f.write("print('cached')")
        
        # Set up mock to indicate successful transform
        mock_transform.return_value = True
        
        # Make notebook file newer than cached script
        os.utime(notebook_path, (os.path.getmtime(script_path) + 10, os.path.getmtime(script_path) + 10))
        
        result_path = nb.build_notebook(self.config, "test")
        
        self.assertEqual(result_path, script_path)
        mock_transform.assert_called_once()
        mock_cache.assert_called_once()

    @mock.patch('nb.transform_notebook')
    @mock.patch('nb.cache_python_files')
    def test_build_notebook_up_to_date(self, mock_cache, mock_transform):
        # Create notebook file
        notebook_path = os.path.join(self.notebooks_path, "test.ipynb")
        with open(notebook_path, "w") as f:
            f.write("{}")  # Empty notebook
        
        # Create cached script
        script_path = os.path.join(self.cache_path, "test.py")
        with open(script_path, "w") as f:
            f.write("print('cached')")
        
        # Make cached script newer than notebook file
        os.utime(script_path, (os.path.getmtime(notebook_path) + 10, os.path.getmtime(notebook_path) + 10))
        
        result_path = nb.build_notebook(self.config, "test")
        
        self.assertEqual(result_path, script_path)
        mock_transform.assert_not_called()  # Should not transform if cache is up to date
        mock_cache.assert_called_once()

    def test_build_notebook_not_found(self):
        with self.assertRaises(SystemExit):
            nb.build_notebook(self.config, "nonexistent")


class TestRunNotebook(unittest.TestCase):
    @mock.patch('os.execv')
    @mock.patch('nb.build_notebook')
    @mock.patch('nb.get_interpreter_path')
    def test_run_notebook(self, mock_get_interpreter, mock_build, mock_execv):
        # Setup mocks
        mock_build.return_value = "/path/to/script.py"
        mock_get_interpreter.return_value = "/path/to/ipython"
        
        config = nb.Config(
            notebooks_path="/path/to/notebooks",
            jupyter_path="/path/to/jupyter",
            ipython_path="/path/to/ipython",
        )
        
        nb.run_notebook(config, "test", ["arg1", "arg2"])
        
        # Check that execv was called with correct arguments
        mock_execv.assert_called_once_with(
            "/path/to/ipython",
            ["ipython", "/path/to/script.py", "arg1", "arg2"]
        )


if __name__ == "__main__":
    unittest.main()