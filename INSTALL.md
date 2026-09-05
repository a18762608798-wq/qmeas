# 安装

仓库：`a18762608798-wq/qmeas`。

## Python venv

修改 `python3` 成具体 python 解释器路径.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 安装

```bash
python3 -m pip install --upgrade --force-reinstall "git+https://github.com/a18762608798-wq/qmeas.git@master"
```

### 更新

直接重跑安装命令即可（`--force-reinstall` 不能省，否则版本号不变时 pip 会判定已满足而跳过）：

```bash
pip install --upgrade --force-reinstall "git+https://github.com/a18762608798-wq/qmeas.git@master"
```

## Julia（通过 CondaPkg.jl）

### 安装

```julia
using CondaPkg

CondaPkg.add("pip")

url = "git+https://github.com/a18762608798-wq/qmeas.git@master"

CondaPkg.withenv() do
    python3 = CondaPkg.which("python3")
    run(`$python3 -m pip install --upgrade $url`)
end
```

### 更新

直接重跑安装代码即可。
