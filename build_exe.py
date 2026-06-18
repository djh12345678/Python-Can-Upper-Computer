# -*- coding: utf-8 -*-
# 文件名: build_exe.py
# 运行此脚本即可自动一键将上位机打包为 Windows EXE 程序

import os
import sys
import subprocess

def main():
    print("=" * 50)
    print("       开始构建上位机 EXE 程序")
    print("=" * 50)
    
    # 1. 检查并安装打包必备工具
    try:
        import PyInstaller
        print("[OK] 检测到 PyInstaller 已安装。")
    except ImportError:
        print("[-] 未检测到 PyInstaller，正在为您自动安装，请稍候...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("[OK] PyInstaller 安装完成！")

    # 2. 准备打包参数
    main_script = "main.py"
    if not os.path.exists(main_script):
        print(f"[x] 错误: 在当前目录下找不到主程序 {main_script} ！")
        input("按回车键退出...")
        return

    # PyInstaller 命令行参数
    # --noconfirm: 自动覆盖旧的打包文件
    # --windowed: 运行时不显示黑色的 DOS 控制台窗口 (发布模式)
    # --onefile: 打包为单独一个 .exe 文件
    pyinstaller_args = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--windowed", 
        "--onefile",
        "--name=UpperComputer_V2",
    ]

    # 添加必备的隐藏依赖 (Hidden Imports)
    # 注: python-can 和 cantools 极度依赖动态导入，
    # 若不加这里，生成的 EXE 运行时可能因为找不到 PCAN 或 DBC 解析模块而闪退。
    hidden_imports = [
        "can.interfaces.pcan",
        "can.interfaces.slcan",
        "can.interfaces.vector",
        "can.interfaces.kvaser",
        "can.interfaces.ixxat",
        "cantools.database.can.formats.dbc",
    ]
    
    for imp in hidden_imports:
        pyinstaller_args.append(f"--hidden-import={imp}")

    # 将入口文件添加在参数最后
    pyinstaller_args.append(main_script)

    print(f"\n[i] 执行打包命令:\n{' '.join(pyinstaller_args)}\n")
    
    # 3. 开始执行打包
    print("=" * 50)
    print("正在打包中（由于包含 PyQt5 等库，这可能需要几十秒到几分钟）...")
    print("请耐心等待，直到提示打包成功...")
    print("=" * 50)
    
    # subprocess.run() 等待其执行完毕
    result = subprocess.run(pyinstaller_args, shell=True)
    
    if result.returncode == 0:
        print("\n\n" + "=" * 50)
        print("[SUCCESS] 恭喜！打包成功！")
        
        target_dir = os.path.abspath("dist")
        exe_path = os.path.join(target_dir, "UpperComputer_V2.exe")
        
        print(f"[i] 您的 EXE 软件已生成在以下路径：\n -> {exe_path}")
        print("=" * 50)
        
        # 自动打开输出目录
        if os.name == 'nt':
            print("正在为您打开所在文件夹...")
            os.startfile(target_dir)
    else:
        print("\n[x] 打包过程中出现错误，请检查上方日志红字报错信息寻找原因。")

    input("\n打包流程结束，按回车键退出...")

if __name__ == "__main__":
    main()
