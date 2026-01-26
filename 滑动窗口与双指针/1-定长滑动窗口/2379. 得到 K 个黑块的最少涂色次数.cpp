class Solution {

public:

    int minimumRecolors(string blocks, int k) {

        int n = blocks.size();

        int cntW = 0;

        // 1) 统计第一个窗口的 W 数量

        for (int i = 0; i < k; i++) {

            if (blocks[i] == 'W') cntW++;

        }

        int ans = cntW;

        // 2) 窗口从左往右滑动

        for (int r = k; r < n; r++) {

            // 新进入的字符 blocks[r]

            if (blocks[r] == 'W') cntW++;

            // 移出去的字符 blocks[r-k]

            if (blocks[r - k] == 'W') cntW--;

            ans = min(ans, cntW);

        }

        return ans;

    }

};