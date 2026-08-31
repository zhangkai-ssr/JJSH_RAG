/** ADS1298 采集示例。 */
#define ADS1298_ID 0x1E

typedef struct {
    int gpio;
} ads_state_t;

static int read_drdy(int gpio)
{
    return gpio == 35;
}
