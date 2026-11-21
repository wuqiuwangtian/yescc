#include <stdio.h>
#include <math.h>

static void moon_phase(int day, char *buf) {
    const char *phases[] = {"🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"};
    int idx = (int)((day % 8 + 8) % 8);
    sprintf(buf, "%s", phases[idx]);
}

int main(void) {
    printf("☕ 月亮来咖啡馆应聘夜班。\n\n");
    printf("老板：你总是缺一块，客人会投诉的。\n");
    printf("月亮：那是我的阴晴圆缺，不是缺勤。\n\n");

    printf("老板翻出排班表，决定先试一个月：\n");
    for (int day = 1; day <= 30; day++) {
        char phase[8];
        moon_phase(day - 1, phase);
        printf("第%2d天 %s", day, phase);
        if (day % 5 == 0) printf("\n");
        else printf("  ");
    }
    printf("\n\n");

    printf("老板：好吧，满月那天你多值一轮。\n");
    printf("月亮：成交。不过工资要按潮汐算。\n");
    printf("☕ 于是，咖啡馆的夜班多了一盏会变形的灯。\n");
    return 0;
}
