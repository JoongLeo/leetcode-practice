class Solution:
    def lengthOfLongestSubstring(self, s: str) -> int:
        # 用哈希表去统计字符出现次数
         # 时间复杂度O（n）
        # 空间复杂度O（128）2^7
        length = 0
        cnt = Counter() # hashmap     k -> char   value-> int
        left = 0 
        for right, c in enumerate(s):
            cnt[c] += 1
            while cnt[c] > 1:
                cnt[s[left]] -= 1
                left += 1
            length = max(length, right - left + 1) # 子串的长度    字符的个数
        return length