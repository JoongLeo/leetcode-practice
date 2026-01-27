// 滑动窗口与双指针
// 一、定长滑动窗口
// 1343. 大小为 K 且平均值大于等于阈值的子数组数目.cpp
class Solution {

public:

    int numOfSubarrays(vector<int>& arr, int k, int threshold) {

        int ans = 0;

        int s = 0; // 维护窗口元素和

        for (int i = 0; i < arr.size(); i++) {

            // 1. 进入窗口

            s += arr[i];

            if (i < k - 1) { // 窗口大小不足 k

                continue;

            }

            // 2. 更新答案

            ans += s >= k * threshold;

            // 3. 离开窗口

            s -= arr[i - k + 1];

        }

        return ans;

    }

};