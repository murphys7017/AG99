import os
import zipfile

from astrbot.core import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_path
from astrbot.core.utils.io import remove_dir

from ..star.star import StarMetadata
from ..updator import RepoZipUpdator


class PluginUpdator(RepoZipUpdator):
    def __init__(self, repo_mirror: str = "", verify: str | bool | None = None) -> None:
        super().__init__(repo_mirror, verify=verify)
        self.plugin_store_path = get_astrbot_plugin_path()

    def get_plugin_store_path(self) -> str:
        return self.plugin_store_path

    async def install(self, repo_url: str, proxy="") -> str:
        _, repo_name, _ = self.parse_github_url(repo_url)
        repo_name = self.format_name(repo_name)
        plugin_path = os.path.join(self.plugin_store_path, repo_name)
        await self.download_from_repo_url(plugin_path, repo_url, proxy)
        self.unzip_file(plugin_path + ".zip", plugin_path)

        return plugin_path

    async def update(self, plugin: StarMetadata, proxy="") -> str:
        repo_url = plugin.repo

        if not repo_url:
            raise Exception(f"插件 {plugin.name} 没有指定仓库地址。")

        if not plugin.root_dir_name:
            raise Exception(f"插件 {plugin.name} 的根目录名未指定。")

        plugin_path = os.path.join(self.plugin_store_path, plugin.root_dir_name)

        logger.info(f"正在更新插件，路径: {plugin_path}，仓库地址: {repo_url}")
        await self.download_from_repo_url(plugin_path, repo_url, proxy=proxy)

        try:
            remove_dir(plugin_path)
        except BaseException as e:
            logger.error(
                f"删除旧版本插件 {plugin_path} 文件夹失败: {e!s}，使用覆盖安装。",
            )

        self.unzip_file(plugin_path + ".zip", plugin_path)

        return plugin_path

    def unzip_file(self, zip_path: str, target_dir: str) -> None:
        os.makedirs(target_dir, exist_ok=True)
        logger.info(f"Extracting archive: {zip_path}")
        with zipfile.ZipFile(zip_path, "r") as z:
            update_dir = self._resolve_archive_root_dir(z.namelist())
            z.extractall(target_dir)

        self._finalize_extracted_archive(zip_path, target_dir, update_dir)
