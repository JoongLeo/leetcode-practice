class Solution {
public:
    int maxVowels(string s, int k) {
        int ans = 0, yuanyin = 0;
        for(int i = 0; i < s.size(); i++){
            if(s[i] == 'a' || s[i] =='e' || s[i] == 'i' || s[i] == 'o' || s[i] == 'u'){
                yuanyin++;
            }
            int left = i - k + 1;
            if(left < 0){
                continue;
            } 
            ans = max(ans, yuanyin);
            if(ans == k){
                break;
            }
            if(s[left] == 'a' || s[left] =='e' || s[left] == 'i' || s[left] == 'o' || s[left] == 'u'){
                yuanyin--;
            }
        }
        return ans;
    }
};