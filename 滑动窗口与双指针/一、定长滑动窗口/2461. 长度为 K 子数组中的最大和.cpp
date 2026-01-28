// 滑动窗口与双指针
// 一、定长滑动窗口
// 2461. 长度为 K 子数组中的最大和.cpp
class Solution {
public:
    long long maximumSubarraySum(vector<int>& nums, int k) {
        long long ans = 0, s = 0;
        unordered_map<int, int> cnt;
        for(int i = 0; i < nums.size(); i++){
            s += nums[i];
            cnt[nums[i]]++;

            int left = i - k + 1;
            if(left < 0){
                continue;
            }

            if(cnt.size() == k){
                ans = max(ans, s);
            }

            int out = nums[left];
            s -= out;
            if(--cnt[out] == 0){
                cnt.erase(out);
            }
        }
        return ans;
    }
};