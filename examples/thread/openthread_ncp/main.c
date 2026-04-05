#include <FreeRTOS.h>
#include <task.h>

#if defined(BL616)
#include "rfparam_adapter.h"
#include "bl616.h"
#elif defined(BL702L)
#include "bl702l.h"
#endif

#include <bl_sys.h>
#include <bflb_mtd.h>

#include <lmac154.h>
#include <zb_timer.h>

#include OPENTHREAD_PROJECT_CORE_CONFIG_FILE
#include <openthread_port.h>
#include <openthread/ncp.h>

#include "board.h"

extern void __libc_init_array(void);
extern void board_ncp_init(void);

void lmac154_app_init(void)
{
    irq_callback lmac154_isr_callback;

    lmac154_init();
    lmac154_enableCoex();
    lmac154_setStd2015Extra(true);
    lmac154_setTxRetry(0);
    lmac154_fptClear();
    lmac154_setEnhAckWaitTime((LMAC154_AIFS + 10 + (6 + 42) * 2) << LMAC154_US_PER_SYMBOL_BITS);
    lmac154_setRxStateWhenIdle(true);

#if defined(BL702L)
    lmac154_setTxRxTransTime(0xA0);
#endif

    zb_timer_cfg(bflb_mtimer_get_time_us() >> LMAC154_US_PER_SYMBOL_BITS);
    lmac154_disableRx();

    lmac154_isr_callback = (irq_callback)lmac154_getInterruptCallback();
    bflb_irq_attach(M154_INT_IRQn, lmac154_isr_callback, NULL);
    bflb_irq_enable(M154_INT_IRQn);
}

void otrInitUser(otInstance * instance)
{
    otAppNcpInit((otInstance * )instance);
}

void vApplicationTickHook( void )
{
#ifdef BL616
    lmac154_monitor(10000);
#endif
}

static void prvInitTask(void *pvParameters)
{
    otRadio_opt_t opt;

    lmac154_app_init();

    opt.byte = 0;

    opt.bf.isCoexEnable = false;
#if OPENTHREAD_FTD
    opt.bf.isFtd = true;
#endif

#if OPENTHREAD_CONFIG_MLE_LINK_METRICS_SUBJECT_ENABLE
    opt.bf.isLinkMetricEnable = true;
#endif
#if OPENTHREAD_CONFIG_MAC_CSL_RECEIVER_ENABLE
    opt.bf.isCSLReceiverEnable = true;
#endif
#if OPENTHREAD_CONFIG_TIME_SYNC_ENABLE
    opt.bf.isTimeSyncEnable = true;
#endif

    struct bflb_device_s *uart0 = bflb_device_get_by_name("uart0");
    ot_uart_init(uart0);

    otrStart(opt);

    vTaskDelete(NULL);
}

int main(void)
{
    bl_sys_rstinfo_init();

    board_ncp_init();

    bflb_mtd_init();

    configASSERT((configMAX_PRIORITIES > 4));

#if defined(BL616)
    /* Init rf */
    if (0 != rfparam_init(0, NULL, 0)) {
        printf("PHY RF init failed!\r\n");
        return 0;
    }
#endif

    __libc_init_array();

    xTaskCreate(prvInitTask, "init", 1024, NULL, 15, NULL);

    puts("[OS] Starting OS Scheduler...\r\n");
    vTaskStartScheduler();

    while (1) {
    }
}
