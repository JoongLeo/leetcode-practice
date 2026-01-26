class Solution:
    def numSubarrayProductLessThanK(self, nums: List[int], k: int) -> int:
        if k <= 1:
            return 0
        ans = 0
        product = 1
        left = 0
        for right, x in enumerate(nums):
            product *= x
            while product >= k:
                product /= nums[left]
                left += 1
            ans += right - left + 1
        return ans