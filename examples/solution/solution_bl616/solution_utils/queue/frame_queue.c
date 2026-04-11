#include "bflb_mtimer.h"
#include "bflb_l1c.h"
#include "bflb_irq.h"

#include <compiler/compiler_gcc.h>
#include <compiler/compiler_ld.h>

#include <FreeRTOS.h>
#include <task.h>
#include <event_groups.h>
#include <queue.h>

#include "frame_queue.h"

#if IS_ENABLED(CONFIG_PSRAM)
#define FRAME_BUFFER_ATTR ATTR_NOINIT_PSRAM_SECTION __ALIGNED(32)
#else
#define FRAME_BUFFER_ATTR __ALIGNED(32)
#endif

/******************* yuyv frame  **********************/
#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_YUYV)

#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_YUYV_SHARE_EN)
FRAME_BUFFER_ATTR uint8_t picture_frame_buffer[1][CONFIG_YUYV_FRAME_SIZE]; /* YUYV raw data */
#else
FRAME_BUFFER_ATTR uint8_t picture_frame_buffer[CONFIG_YUYV_FRAME_NUM][CONFIG_YUYV_FRAME_SIZE]; /* YUYV raw data */
#endif

frame_queue_ctrl_t *g_yuyv_frame_ctrl = NULL;

int yuyv_frame_ctrl_init(void)
{
    int ret;
    printf("yuyv_frame_ctrl_init\r\n");

    /* fill info */
    pyuyv_frame_t frame_desc[CONFIG_YUYV_FRAME_NUM];
    for (uint16_t i = 0; i < CONFIG_YUYV_FRAME_NUM; i++) {
        frame_desc[i].elem_base.frame_id = i;
        frame_desc[i].elem_base.frame_size = CONFIG_YUYV_FRAME_SIZE;
#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_YUYV_SHARE_EN)
        frame_desc[i].elem_base.frame_addr = picture_frame_buffer[0];
#else
        frame_desc[i].elem_base.frame_addr = picture_frame_buffer[i];
#endif
        frame_desc[i].x_start = 0;
        frame_desc[i].y_start = 0;
        frame_desc[i].x_end = 0;
        frame_desc[i].y_end = 0;
    }

    /* create ctrl */
    ret = frame_queue_create(&g_yuyv_frame_ctrl, CONFIG_YUYV_FRAME_NUM, sizeof(pyuyv_frame_t), (void *)frame_desc, "yuyv");

    if (ret < 0) {
        printf("yuyv frame ctrl create failed\r\n");
    }

    return ret;
}
#endif

/******************** jpeg frame **********************/

#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_MJPEG)

FRAME_BUFFER_ATTR uint8_t jpeg_frame_buffer[CONFIG_MJPEG_FRAME_NUM][CONFIG_MJPEG_FRAME_SIZE]; /* Multi buffer queue */

frame_queue_ctrl_t *g_jpeg_frame_ctrl = NULL;

int jpeg_frame_ctrl_init(void)
{
    int ret;

    printf("jpeg_frame_ctrl_init\r\n");

    /* fill info */
    jpeg_frame_t frame_desc[CONFIG_MJPEG_FRAME_NUM];
    for (uint16_t i = 0; i < CONFIG_MJPEG_FRAME_NUM; i++) {
        frame_desc[i].elem_base.frame_id = i;
        frame_desc[i].elem_base.frame_size = CONFIG_MJPEG_FRAME_SIZE;
        frame_desc[i].elem_base.frame_addr = jpeg_frame_buffer[i];
        frame_desc[i].data_size = 0;
    }

    /* create ctrl */
    ret = frame_queue_create(&g_jpeg_frame_ctrl, CONFIG_MJPEG_FRAME_NUM, sizeof(jpeg_frame_t), (void *)frame_desc, "jpeg");

    if (ret < 0) {
        printf("jpeg frame ctrl create failed\r\n");
    }

    return ret;
}

#endif

/******************* audio input frame  **********************/
#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_AUDIO_IN)

FRAME_BUFFER_ATTR uint8_t auadc_frame_buffer[CONFIG_AUDIO_IN_FRAME_NUM][CONFIG_AUDIO_IN_FRAME_SIZE]; /* Multi buffer queue */

frame_queue_ctrl_t *g_audio_in_frame_ctrl = NULL;

int auadc_in_frame_ctrl_init(void)
{
    int ret;

    printf("auadc_in_frame_ctrl_init\r\n");

    /* fill info */
    audio_in_frame_t frame_desc[CONFIG_AUDIO_IN_FRAME_NUM];
    for (uint16_t i = 0; i < CONFIG_AUDIO_IN_FRAME_NUM; i++) {
        frame_desc[i].elem_base.frame_id = i;
        frame_desc[i].elem_base.frame_size = CONFIG_AUDIO_IN_FRAME_SIZE;
        frame_desc[i].elem_base.frame_addr = auadc_frame_buffer[i];
        frame_desc[i].data_size = 0;
        frame_desc[i].frame_mode = 0;
    }

    /* create ctrl */
    ret = frame_queue_create(&g_audio_in_frame_ctrl, CONFIG_AUDIO_IN_FRAME_NUM, sizeof(audio_in_frame_t), (void *)frame_desc, "audio_in");

    if (ret < 0) {
        printf("audio input frame ctrl create failed\r\n");
    }

    return ret;
}

#endif

/******************* audio output frame  **********************/
#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_AUDIO_OUT)

FRAME_BUFFER_ATTR uint8_t audac_frame_buffer[CONFIG_AUDIO_OUT_FRAME_NUM][CONFIG_AUDIO_OUT_FRAME_SIZE]; /* Multi buffer queue */

frame_queue_ctrl_t *g_audio_out_frame_ctrl = NULL;

int auadc_out_frame_ctrl_init(void)
{
    int ret;

    printf("auadc_out_frame_ctrl_init\r\n");

    /* fill info */
    audio_out_frame_t frame_desc[CONFIG_AUDIO_OUT_FRAME_NUM];
    for (uint16_t i = 0; i < CONFIG_AUDIO_OUT_FRAME_NUM; i++) {
        frame_desc[i].elem_base.frame_id = i;
        frame_desc[i].elem_base.frame_size = CONFIG_AUDIO_OUT_FRAME_SIZE;
        frame_desc[i].elem_base.frame_addr = audac_frame_buffer[i];
        frame_desc[i].data_size = 0;
        frame_desc[i].frame_mode = 0;
    }

    /* create ctrl */
    ret = frame_queue_create(&g_audio_out_frame_ctrl, CONFIG_AUDIO_OUT_FRAME_NUM, sizeof(audio_out_frame_t), (void *)frame_desc, "audio_out");

    if (ret < 0) {
        printf("audio output frame ctrl create failed\r\n");
    }

    return ret;
}

#endif

/******************* all frame init  **********************/
int frame_ctrl_init_all(void)
{
    int ret;
    (void)ret;

    printf("frame_ctrl_init_all\r\n");

#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_YUYV)
    ret = yuyv_frame_ctrl_init();
    if (ret < 0) {
        return ret;
    }

#endif

#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_MJPEG)
    ret = jpeg_frame_ctrl_init();
    if (ret < 0) {
        return ret;
    }

#endif

#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_AUDIO_IN)
    ret = auadc_in_frame_ctrl_init();
    if (ret < 0) {
        return ret;
    }
#endif

#if IS_ENABLED(CONFIG_SOLUTION_QUEUE_AUDIO_OUT)
    ret = auadc_out_frame_ctrl_init();
    if (ret < 0) {
        return ret;
    }
#endif

    return 0;
}
