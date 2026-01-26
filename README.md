# LeetCode 提交自动同步

自动将 LeetCode 的 AC 提交同步到 GitHub 仓库，支持自定义分类。

## 功能特点

- ✅ 自动同步 LeetCode CN 和 Global 的 AC 提交
- ✅ 根据代码注释自动分类（支持一级/二级分类）
- ✅ 避免重复同步
- ✅ 每天自动运行两次
- ✅ 支持手动触发

## 分类规则

在代码第一行添加注释来指定分类：

```cpp
// 滑动窗口与双指针 / 一、定长滑动窗口
class Solution {
    // your code
};
