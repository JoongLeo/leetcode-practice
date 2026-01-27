// 滑动窗口与双指针
// 一、定长滑动窗口
// 643. 子数组最大平均数 I.cpp
class Solution {

public:

    double findMaxAverage(vector<int>& nums, int k) {

        int max_s = INT_MIN; // 窗口元素和的最大值

        int s = 0; // 维护窗口元素和

        for (int i = 0; i < nums.size(); i++) {

            // 1. 进入窗口

            s += nums[i];

            if (i < k - 1) { // 窗口大小不足 k

                continue;

            }

            // 2. 更新答案

            max_s = max(max_s, s);

            // 3. 离开窗口

            s -= nums[i - k + 1];

        }

        return (double) max_s / k;

    }

};