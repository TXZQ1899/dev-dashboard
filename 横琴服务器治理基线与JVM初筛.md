# 横琴服务器治理基线与 JVM 初筛

基于此前明确指定的 117 个唯一 IP，来源为 `outputs/hengqin-ecs-governance/横琴ECS治理_117台清单与进程明细.xlsx`；原名单称 118 台，但只有 117 个唯一 IP。本报告保留其中 4 台同时关联集团、商旅和其它的混合归属，待确认，未自行剔除或补入。数据对应 2026-11-17 导出及进程采集，并非实时数据。

## 治理前已分配资源

| 原表业务分类 | 服务器数 | 已分配 vCPU | 已分配内存 GiB |
|---|---:|---:|---:|
| 其它 | 39 | 162 | 572 |
| 未识别 | 30 | 110 | 339 |
| 集团 | 31 | 152 | 504 |
| 集团、其它 | 13 | 84 | 456 |
| 集团、商旅、其它 | 4 | 30 | 156 |
| **合计** | **117** | **538** | **2027** |

上述为配置容量，不是 CPU 使用率。其余 46 台为 386 vCPU、1,740 GiB；横琴租户合计 163 台、924 vCPU、3,767 GiB。混合分类不重复计入。未包含账单、磁盘和带宽。

## 多节点应用待核查

按进程表中相同应用 ID 与不同服务器 IP 形成候选，仅用于复核；尚未核对环境、节点身份及当前在役状态，不能据此减少节点。

| 应用 ID | 应用名称 | 观察到的不同 IP 数 | IP |
|---|---|---:|---|
| 1504 | photo-standard-job | 4 | 10.179.4.165、10.179.4.72、10.179.5.153、10.179.5.154 |
| 1882 | fcrm-job | 4 | 10.179.4.179、10.179.4.194、10.179.5.151、10.179.5.152 |
| 1412 | dolphin-switches（推断） | 3 | 10.179.4.162、10.179.4.192、10.179.4.193 |
| 1431 | uc-facade | 3 | 10.179.4.204、10.179.5.150、10.179.5.152 |
| 1432 | uc-online | 3 | 10.179.5.149、10.179.5.150、10.179.5.152 |
| 1433 | app-api | 3 | 10.179.5.149、10.179.5.150、10.179.5.151 |
| 1440 | fomall-gateway、fomall-gateway（推断） | 3 | 10.179.2.10、10.179.5.146、10.179.5.148 |
| 1458 | smart-sso-server（推断） | 3 | 10.179.2.30、10.179.5.149、10.179.5.150 |
| 1851 | foliday-mms（推断） | 3 | 10.179.5.146、10.179.5.151、10.179.5.152 |
| 1901 | merchant-processor | 3 | 10.179.4.108、10.179.4.81、10.179.4.91 |
| 1911 | fosun-aggregation-openapi | 3 | 10.179.5.146、10.179.5.147、10.179.5.148 |
| 1368 | fosun-merchant | 2 | 10.179.5.146、10.179.5.148 |
| 1372 | buyer-api | 2 | 10.179.5.146、10.179.5.148 |
| 1373 | base-api | 2 | 10.179.5.146、10.179.5.148 |
| 1374 | cashier-api | 2 | 10.179.5.146、10.179.5.148 |
| 1376 | consumer | 2 | 10.179.5.146、10.179.5.148 |
| 1377 | manager-api | 2 | 10.179.5.146、10.179.5.148 |
| 1378 | mbp-seller-api | 2 | 10.179.5.146、10.179.5.148 |
| 1380 | marketing-center-offline | 2 | 10.179.5.149、10.179.5.150 |
| 1382 | marketing-center-activity-front-service | 2 | 10.179.5.149、10.179.5.150 |
| 1395 | fostay-online-order（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1396 | fostay-online-product（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1398 | fostay-sys-order（推断） | 2 | 10.179.5.145、10.179.5.151 |
| 1404 | mebc-order-service | 2 | 10.179.5.149、10.179.5.150 |
| 1405 | member-center-sync | 2 | 10.179.5.149、10.179.5.150 |
| 1406 | mebc-growth-service | 2 | 10.179.5.149、10.179.5.150 |
| 1407 | member-center-offline | 2 | 10.179.5.149、10.179.5.150 |
| 1408 | mebc-privilege-service | 2 | 10.179.5.149、10.179.5.150 |
| 1411 | dolphin-open（推断） | 2 | 10.179.4.139、10.179.4.196 |
| 1422 | fcrs-base | 2 | 10.179.5.145、10.179.5.147 |
| 1423 | fcrs-channel | 2 | 10.179.5.145、10.179.5.147 |
| 1424 | fcrs-fpms | 2 | 10.179.5.145、10.179.5.147 |
| 1425 | fcrs-product-pump | 2 | 10.179.5.145、10.179.5.147 |
| 1427 | fsop-productcenter（推断） | 2 | 10.179.5.146、10.179.5.148 |
| 1428 | online-search（推断） | 2 | 10.179.5.146、10.179.5.148 |
| 1429 | uc-admin | 2 | 10.179.5.149、10.179.5.150 |
| 1430 | uc-oapi | 2 | 10.179.5.149、10.179.5.150 |
| 1434 | app-admin | 2 | 10.179.5.149、10.179.5.150 |
| 1438 | fothing-payment（推断） | 2 | 10.179.5.146、10.179.5.148 |
| 1441 | dolphin-webhook（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1442 | cls-api | 2 | 10.179.5.150、10.179.5.151 |
| 1446 | fosun-settlement | 2 | 10.179.5.146、10.179.5.148 |
| 1447 | fosun-cashier | 2 | 10.179.5.146、10.179.5.148 |
| 1452 | dolphin-task-service（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1453 | fcrm | 2 | 10.179.5.149、10.179.5.150 |
| 1460 | foliday_meb（推断） | 2 | 10.179.5.149、10.179.5.150 |
| 1462 | discovery | 2 | 10.179.4.108、10.179.4.251 |
| 1464 | coupon-web | 2 | 10.179.5.149、10.179.5.150 |
| 1465 | coupon-manage | 2 | 10.179.5.149、10.179.5.150 |
| 1466 | coupon-consumer | 2 | 10.179.5.149、10.179.5.150 |
| 1467 | service-api | 2 | 10.179.5.149、10.179.5.150 |
| 1468 | equity-manage | 2 | 10.179.5.149、10.179.5.150 |
| 1469 | equity-web | 2 | 10.179.5.149、10.179.5.150 |
| 1470 | fcrm-consumer | 2 | 10.179.5.149、10.179.5.150 |
| 1471 | point-web | 2 | 10.179.5.149、10.179.5.150 |
| 1472 | point-manage | 2 | 10.179.5.149、10.179.5.150 |
| 1475 | ups-api | 2 | 10.179.5.149、10.179.5.150 |
| 1476 | fmsg-service | 2 | 10.179.5.145、10.179.5.147 |
| 1477 | fmsg-web | 2 | 10.179.5.145、10.179.5.147 |
| 1483 | dolphin-task（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1485 | foyue-openapi | 2 | 10.179.5.151、10.179.5.152 |
| 1488 | foyue-manage | 2 | 10.179.5.151、10.179.5.152 |
| 1489 | merchant-api | 2 | 10.179.5.151、10.179.5.152 |
| 1490 | foliday-photo-opeadmin | 2 | 10.179.4.165、10.179.4.72 |
| 1495 | hotel-cms（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1496 | spark-api | 2 | 10.179.4.79、10.179.4.80 |
| 1497 | foliday-photo-web | 2 | 10.179.4.165、10.179.4.72 |
| 1500 | foliday-photo-sync | 2 | 10.179.4.165、10.179.4.72 |
| 1509 | hotel-ari-abut（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1510 | hotel-ari-task（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1512 | spark-admin | 2 | 10.179.4.79、10.179.4.80 |
| 1514 | mebc-user-service | 2 | 10.179.5.149、10.179.5.150 |
| 1516 | message-center-admin | 2 | 10.179.5.146、10.179.5.148 |
| 1517 | message-center-api | 2 | 10.179.5.146、10.179.5.148 |
| 1518 | message-center-consumer | 2 | 10.179.5.146、10.179.5.148 |
| 1521 | dolphin-mq（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1531 | phoenix-fliggyapi（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1533 | finance-check | 2 | 10.179.5.146、10.179.5.148 |
| 1534 | foliday-sms（推断） | 2 | 10.179.5.146、10.179.5.148 |
| 1535 | fosun-payment | 2 | 10.179.5.146、10.179.5.148 |
| 1537 | crm-markting-notify-api | 2 | 10.179.5.149、10.179.5.150 |
| 1553 | hotel-ari-api（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1560 | phoenix-online-product（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1565 | alipaymini-web | 2 | 10.179.5.149、10.179.5.150 |
| 1571 | dubbo-adaptor-web（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1573 | phoenix-online-order（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1598 | cooperate-union-service | 2 | 10.179.5.149、10.179.5.150 |
| 1617 | log-service（推断） | 2 | 10.179.5.146、10.179.5.148 |
| 1637 | alipaymini-admin | 2 | 10.179.5.149、10.179.5.150 |
| 1646 | ctrip-api | 2 | 10.179.5.145、10.179.5.147 |
| 1650 | active-marketing-service | 2 | 10.179.5.149、10.179.5.150 |
| 1656 | phoenix-mappingtool（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1663 | public-online | 2 | 10.179.5.146、10.179.5.148 |
| 1666 | short-links-web | 2 | 10.179.4.154、10.179.4.238 |
| 1669 | mebc-meb-service | 2 | 10.179.5.149、10.179.5.150 |
| 1672 | product-activity-service | 2 | 10.179.5.149、10.179.5.150 |
| 1762 | user-account-center-admin | 2 | 10.179.4.79、10.179.4.80 |
| 1804 | marketing-equity-front-service | 2 | 10.179.5.149、10.179.5.150 |
| 1839 | mebc-point-service | 2 | 10.179.5.149、10.179.5.150 |
| 1860 | live-center-offline | 2 | 10.179.5.149、10.179.5.150 |
| 1861 | live-center-service | 2 | 10.179.4.155、10.179.4.206 |
| 1874 | phoenix-open-web（推断） | 2 | 10.179.5.145、10.179.5.147 |
| 1899 | mebc-meb-processor | 2 | 10.179.4.179、10.179.4.194 |
| 1904 | alipaymini-processor | 2 | 10.179.5.149、10.179.5.150 |
| 1910 | fosun-aggregation-facade | 2 | 10.179.5.146、10.179.5.147 |
| 709 | security-web（推断） | 2 | 10.179.5.146、10.179.5.148 |

## JVM 内存待评估清单

以下 64 条按示例筛选条件列出：显式 Xmx 至少 2 GiB，单次进程 RSS 不超过 Xmx 的一半。该比例仅为排查顺序，不是堆利用率、可回收内存或调参建议；待确认或可能退出的进程需先复核身份。须结合 GC 后堆、峰值及非堆用量再决定是否调参。

| IP | PID | 进程 / 应用 | 来源 | Xmx | RSS MiB |
|---|---:|---|---|---:|---:|
| 10.179.3.14 | 198227 | 待核实 | 待确认进程 | 4 GiB | 446.1 |
| 10.179.4.47 | 27177 | 待核实 | 待确认进程 | 2 GiB | 333.1 |
| 10.179.3.14 | 199196 | 待核实 | 待确认进程 | 2 GiB | 507.8 |
| 10.179.2.10 | 3929 | 待核实 | 待确认进程 | 3 GiB | 789.5 |
| 10.179.1.224 | 17467 | 待核实 | 待确认进程 | 3 GiB | 860.2 |
| 10.179.4.206 | 9428 | /opt/tomcat | 待确认进程 | 3 GiB | 866.3 |
| 10.179.2.30 | 734 | 待核实 | 待确认进程 | 3 GiB | 887.1 |
| 10.179.4.47 | 16584 | 待核实 | 待确认进程 | 3 GiB | 949.8 |
| 10.179.2.30 | 14015 | smart-sso-server（推断） | 业务应用候选 | 3.09766 GiB | 1029.7 |
| 10.179.5.150 | 31274 | smart-sso-server（推断） | 业务应用候选 | 3 GiB | 1081.1 |
| 10.179.5.149 | 7851 | smart-sso-server（推断） | 业务应用候选 | 3 GiB | 1131.4 |
| 10.179.2.30 | 15191 | expense-service（推断） | 业务应用候选 | 3.09766 GiB | 1234.3 |
| 10.179.4.148 | 28118 | 待核实 | 待确认进程 | 2 GiB | 802.8 |
| 10.179.5.146 | 1240 | foliday-mms（推断） | 业务应用候选 | 3 GiB | 1214 |
| 10.179.5.145 | 17896 | hotel-ari-api（推断） | 业务应用候选 | 3 GiB | 1216.4 |
| 10.179.5.147 | 9139 | hotel-ari-api（推断） | 业务应用候选 | 3 GiB | 1219.1 |
| 10.179.4.251 | 32736 | questionnaire-survey | 业务应用候选 | 3 GiB | 1222.4 |
| 10.179.5.152 | 31885 | foliday-mms（推断） | 业务应用候选 | 3 GiB | 1227.9 |
| 10.179.4.79 | 3344 | user-account-center-admin | 业务应用候选 | 3 GiB | 1245.1 |
| 10.179.2.141 | 14768 | /opt/apache-tomcat_uk-fosun-uaa_35033 | 业务应用候选 | 2 GiB | 830.2 |
| 10.179.4.80 | 4844 | user-account-center-admin | 业务应用候选 | 3 GiB | 1254.4 |
| 10.179.5.148 | 24748 | fosun-aggregation-processor | 业务应用候选 | 3 GiB | 1272.5 |
| 10.179.5.147 | 11117 | phoenix-open-web（推断） | 业务应用候选 | 3 GiB | 1286.5 |
| 10.179.5.149 | 1789 | coupon-config | 业务应用候选 | 3 GiB | 1301 |
| 10.179.5.146 | 16141 | fosun-aggregation-openapi | 业务应用候选 | 3 GiB | 1304.1 |
| 10.179.5.146 | 6161 | security-web（推断） | 业务应用候选 | 3.09766 GiB | 1351.8 |
| 10.179.5.147 | 12972 | fosun-aggregation-openapi | 业务应用候选 | 3 GiB | 1311.5 |
| 10.179.5.148 | 4684 | security-web（推断） | 业务应用候选 | 3.09766 GiB | 1355.8 |
| 10.179.5.153 | 15880 | photo-standard-job | 业务应用候选 | 3 GiB | 1322.7 |
| 10.179.5.148 | 2158 | fosun-aggregation-openapi | 业务应用候选 | 3 GiB | 1323.9 |
| 10.179.2.30 | 29414 | flight-web-api | 业务应用候选 | 4 GiB | 1783.3 |
| 10.179.5.154 | 17518 | photo-standard-job | 业务应用候选 | 3 GiB | 1339.4 |
| 10.179.1.224 | 10687 | /opt/apache-tomcat_security-web_37859 | 业务应用候选 | 3.09766 GiB | 1389.5 |
| 10.179.5.151 | 32487 | foliday-mms（推断） | 业务应用候选 | 3 GiB | 1358.1 |
| 10.179.2.30 | 4779 | platform-web（推断） | 业务应用候选 | 4 GiB | 1813.1 |
| 10.179.5.148 | 23923 | foliday-sms（推断） | 业务应用候选 | 3 GiB | 1365.8 |
| 10.179.5.145 | 3403 | phoenix-open-web（推断） | 业务应用候选 | 3 GiB | 1376.3 |
| 10.179.5.146 | 1070 | tims-finance-web（推断） | 业务应用候选 | 3 GiB | 1376.6 |
| 10.179.5.147 | 23377 | phoenix-online-product（推断） | 业务应用候选 | 3 GiB | 1390.5 |
| 10.179.5.152 | 29644 | foyue-manage | 业务应用候选 | 2 GiB | 935.6 |
| 10.179.4.196 | 22685 | dolphin-open（推断） | 业务应用候选 | 10 GiB | 4701 |
| 10.179.5.147 | 1854 | fcrs-channel | 业务应用候选 | 3 GiB | 1410.5 |
| 10.179.5.145 | 22538 | fcrs-channel | 业务应用候选 | 3 GiB | 1412.2 |
| 10.179.2.14 | 112113 | 待核实 | 待确认进程 | 3.90625 GiB | 1843.5 |
| 10.179.5.145 | 21435 | phoenix-online-product（推断） | 业务应用候选 | 3 GiB | 1418.1 |
| 10.179.5.151 | 9662 | foyue-manage | 业务应用候选 | 2 GiB | 947.3 |
| 10.179.5.146 | 2463 | message-center-admin | 业务应用候选 | 3 GiB | 1423.6 |
| 10.179.5.148 | 14954 | message-center-admin | 业务应用候选 | 3 GiB | 1426.8 |
| 10.179.5.145 | 10968 | fostay-online-product（推断） | 业务应用候选 | 3 GiB | 1443.5 |
| 10.179.5.148 | 32704 | fosun-payment | 业务应用候选 | 3 GiB | 1445.5 |
| 10.179.5.147 | 17413 | fostay-online-product（推断） | 业务应用候选 | 3 GiB | 1448.4 |
| 10.179.5.146 | 19548 | fosun-payment | 业务应用候选 | 3 GiB | 1449.9 |
| 10.179.5.146 | 27478 | foliday-sms（推断） | 业务应用候选 | 3 GiB | 1472.7 |
| 10.179.5.148 | 16661 | mbp-seller-api | 业务应用候选 | 4 GiB | 1976.8 |
| 10.179.2.10 | 21194 | fomall-gateway（推断） | 业务应用候选 | 2 GiB | 989.3 |
| 10.179.2.30 | 11838 | hotel-datasync | 业务应用候选 | 4 GiB | 1985.7 |
| 10.179.4.162 | 20140 | dolphin-switches（推断） | 业务应用候选 | 13 GiB | 6498.8 |
| 10.179.5.145 | 5091 | dolphin-task（推断） | 业务应用候选 | 4 GiB | 2002.5 |
| 10.179.5.147 | 21739 | hotel-ari-task（推断） | 业务应用候选 | 3 GiB | 1512.5 |
| 10.179.5.152 | 29895 | foyue-openapi | 业务应用候选 | 2 GiB | 1013.8 |
| 10.179.4.137 | 25558 | /opt/tomcat | 待确认进程 | 3 GiB | 1523.7 |
| 10.179.2.14 | 108264 | 待核实 | 待确认进程 | 3.90625 GiB | 1984.8 |
| 10.179.5.145 | 31067 | hotel-cms（推断） | 业务应用候选 | 3 GiB | 1524.7 |
| 10.179.1.224 | 29214 | www（推断） | 业务应用候选 | 2 GiB | 1021.4 |

## JVM 参数与进程内存清单

以下保留进程表中含 JVM 堆参数或明确 Java/Tomcat 进程的记录。RSS 是当次进程驻留内存，包含非堆和共享页；不能将 RSS 与 Xmx 相减当作可释放堆，也不能由单次 RSS 确定可调小的配置。应用关联、进程身份、共享 Tomcat 及环境以原表为准；待确认进程不得按确定应用处理。建议结合业务峰值、GC 后堆占用、GC 停顿和重启验证后确定调整值。

| IP | PID | 进程标识 | 应用 ID / 名称 | 来源 | JVM 参数 | RSS MiB |
|---|---:|---|---|---|---|---:|
| 10.179.1.224 | 10687 | /opt/apache-tomcat_security-web_37859 | 待核实 / 待核实 | 业务应用候选 | -Xmx3172M -Xms1024M -Xss256k | 1389.5 |
| 10.179.1.224 | 13939 | short-links-web-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xmx2G -Xms1G | 1471.3 |
| 10.179.1.224 | 15168 | nacos-server.jar | 待核实 / 待核实 | 待确认进程 | -Xms512m -Xmx512m -Xmn256m | 973.1 |
| 10.179.1.224 | 16947 | /opt/apache-tomcat_phoenix-mappingtool_37863 | 待核实 / 待核实 | 业务应用候选 | -Xmx2048M -Xms1024M -Xss256k | 2250.8 |
| 10.179.1.224 | 17467 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2048m -Xmx3072m -Xmn1024m -Xss256k | 860.2 |
| 10.179.1.224 | 17506 | ZooKeeper | 待核实 / ZooKeeper（启动命令识别的组件） | 待确认进程 | -Xmx1000m | 247.9 |
| 10.179.1.224 | 17614 | fcrs-channel-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xmx2G -Xms1G | 1677.8 |
| 10.179.1.224 | 17629 | fosun-wechat-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xmx1G -Xms1G | 1432.1 |
| 10.179.1.224 | 28746 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx1G -Xms1G | 1346.9 |
| 10.179.1.224 | 29214 | /opt/apache-tomcat_phoenix-open-web_37864 | 1869 / www（推断） | 业务应用候选 | -Xmx2048M -Xms1024M -Xss256k | 1021.4 |
| 10.179.1.224 | 3228 | /opt/tomcat | 待核实 / 待核实 | 待确认进程 | -Xms1024M -Xmx1024M | 616.3 |
| 10.179.1.224 | 4312 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2048m -Xmx2048m -Xmn1024m | 1456.5 |
| 10.179.1.224 | 6618 | /opt/tomcat | 待核实 / 待核实 | 待确认进程 | 未显式声明，JVM 生效值未核实 | 113.4 |
| 10.179.1.224 | 6838 | fcrs-base-exec.jar | 1869 / www（推断） | 业务应用候选 | -Xmx2G -Xms1G | 1763.2 |
| 10.179.1.224 | 7036 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2048m -Xmx3072m -Xmn1024m -Xss256k | 3096.3 |
| 10.179.1.226 | 8033 | jenkins.war | 待核实 / 待核实 | 待确认进程 | -Xmx2g -Xms1g | 2868 |
| 10.179.1.227 | 21702 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms3g -Xmx3g -Xss1m -XX:MaxDirectMemorySize=1610612736 | 3916.6 |
| 10.179.1.227 | 2893 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms1g -Xmx1g | 1554.3 |
| 10.179.1.236 | 5836 | /opt/apache-tomcat_phoenix-offline-product_36748 | 1816 / phoenix-offline-product-废弃（推断） | 业务应用候选 | -Xmx2048M -Xms1024M -Xss256k | 1305.6 |
| 10.179.2.10 | 13794 | apollo-configservice-1.7.1.jar | 待核实 / 待核实 | 待确认进程 | 未显式声明，JVM 生效值未核实 | 332.5 |
| 10.179.2.10 | 14057 | apollo-adminservice-1.7.1.jar | 待核实 / 待核实 | 待确认进程 | 未显式声明，JVM 生效值未核实 | 371.7 |
| 10.179.2.10 | 14291 | apollo-portal-1.7.1.jar | 待核实 / 待核实 | 待确认进程 | 未显式声明，JVM 生效值未核实 | 289.7 |
| 10.179.2.10 | 21194 | fomall-gateway-exec.jar | 1440 / fomall-gateway（推断） | 业务应用候选 | -Xmx2G -Xms2G | 989.3 |
| 10.179.2.10 | 2312 | nacos-server.jar | 待核实 / 待核实 | 待确认进程 | -Xms256m -Xmx256m -Xmn1g | 529.2 |
| 10.179.2.10 | 29718 | ZooKeeper | 待核实 / ZooKeeper（启动命令识别的组件） | 待确认进程 | -Xmx1000m | 174.1 |
| 10.179.2.10 | 3929 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2048m -Xmx3072m -Xmn1024m -Xss256k | 789.5 |
| 10.179.2.10 | 4165 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms512m -Xmx512m | 626.7 |
| 10.179.2.14 | 108264 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xss512k -Xms4000m -Xmx4000m -Xmn1500m -XX:MaxMetaspaceSize=800m | 1984.8 |
| 10.179.2.14 | 112113 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xss512k -Xms4000m -Xmx4000m -Xmn1500m -XX:MaxMetaspaceSize=800m | 1843.5 |
| 10.179.2.14 | 115239 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xss512k -Xms4000m -Xmx4000m -Xmn1500m -XX:MaxMetaspaceSize=800m | 2140.1 |
| 10.179.2.14 | 124520 | nacos-server | 待核实 / 待核实 | 待确认进程 | -Xms512m -Xmx512m -Xmn256m | 1087.3 |
| 10.179.2.14 | 13112 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2g -Xmx3g -Xss256k | 2430 |
| 10.179.2.14 | 46084 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xss512k -Xms4000m -Xmx4000m -Xmn1500m -XX:MaxMetaspaceSize=800m | 3081.6 |
| 10.179.2.14 | 47303 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xss512k -Xms60m -Xmx1000m -XX:MaxMetaspaceSize=512m | 426.6 |
| 10.179.2.14 | 64274 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xss512k -Xms4000m -Xmx4000m -Xmn1500m -XX:MaxMetaspaceSize=800m | 4940.3 |
| 10.179.2.14 | 71475 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xss512k -Xms60m -Xmx1000m -XX:MaxMetaspaceSize=512m | 1040.1 |
| 10.179.2.14 | 75784 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xss512k -Xms4000m -Xmx4000m -Xmn1500m -XX:MaxMetaspaceSize=800m | 5543.2 |
| 10.179.2.14 | 81980 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xss512k -Xms4000m -Xmx4000m -Xmn1500m -XX:MaxMetaspaceSize=800m | 2244.3 |
| 10.179.2.14 | 91669 | -Dspring.profiles.active=uat | 待核实 / 待核实 | 待确认进程 | -Xms2g -Xmx4g | 2412.8 |
| 10.179.2.141 | 14768 | /opt/apache-tomcat_uk-fosun-uaa_35033 | 待核实 / 待核实 | 业务应用候选 | -Xmx2048M -Xms1024M -Xss256k | 830.2 |
| 10.179.2.141 | 5043 | ZooKeeper | 待核实 / ZooKeeper（启动命令识别的组件） | 待确认进程 | -Xmx1000m -Xmx1000m | 85.9 |
| 10.179.2.143 | 2736 | ZooKeeper | 待核实 / ZooKeeper（启动命令识别的组件） | 待确认进程 | -Xmx1000m | 123.4 |
| 10.179.2.144 | 3275 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms1024m -Xmx1024m -Xmn1024m -Xss256k | 996.9 |
| 10.179.2.152 | 3011 | /opt/apache-tomcat-cmdb | 待核实 / 待核实 | 业务应用候选 | -Xmx4096M -Xms4096M -Xss256k | 4778 |
| 10.179.2.152 | 3405 | activemq.jar | 待核实 / 待核实 | 待确认进程 | -Xms64M -Xmx1G | 282.2 |
| 10.179.2.152 | 3644 | /opt/apache-tomcat-theone | 待核实 / 待核实 | 业务应用候选 | -Xmx4096M -Xms4096M -Xss256k | 4665.4 |
| 10.179.2.155 | 21245 | /opt/apache-tomcat-cat | 1865 / cat（推断） | 业务应用候选 | -Xms10G -Xmx10G -Xss256k | 8850.8 |
| 10.179.2.156 | 12915 | /opt/apache-tomcat-mneighbor | 待核实 / 待核实 | 业务应用候选 | -Xmx4096M -Xms4096M -Xss256k | 4819.2 |
| 10.179.2.156 | 18285 | xdd-admin_xddprod.jar | 待核实 / 待核实 | 待确认进程 | -Xmx4096M -Xms4096M -Xss256k | 2065.6 |
| 10.179.2.156 | 2224 | activemq.jar | 待核实 / 待核实 | 待确认进程 | -Xms64M -Xmx1G | 360.6 |
| 10.179.2.30 | 11301 | cbp-service-exec.jar | 1746 / cbp-service | 业务应用候选 | -Xmx512M -Xms512M | 648.4 |
| 10.179.2.30 | 11576 | pss-admin-web-exec.jar | 1801 / pss-admin-web | 业务应用候选 | -Xmx2G -Xms1G | 1056.9 |
| 10.179.2.30 | 11662 | finance-center-admin-exec.jar | 1480 / finance-center-admin | 业务应用候选 | -Xmx2G -Xms1G | 2026.1 |
| 10.179.2.30 | 11682 | /opt/apache-tomcat_hse-web-api | 1709 / hse-web-api（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 841.7 |
| 10.179.2.30 | 11715 | payment-service-exec.jar | 1753 / payment-service | 业务应用候选 | -Xmx1024M -Xms1024M | 782.6 |
| 10.179.2.30 | 11838 | hotel-datasync-exec.jar | 1862 / hotel-datasync | 业务应用候选 | -Xmx4096M -Xms4096M | 1985.7 |
| 10.179.2.30 | 11963 | share-fbp-service-exec.jar | 1736 / share-fbp-service（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 1422.4 |
| 10.179.2.30 | 12213 | tbp-service-exec.jar | 1722 / tbp-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 656 |
| 10.179.2.30 | 13013 | /opt/apache-tomcat_ec-web-server | 1734 / ec-web-server（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 831.6 |
| 10.179.2.30 | 13029 | ftrip-log-service-exec.jar | 1694 / ftrip-log-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 600.4 |
| 10.179.2.30 | 13270 | /opt/apache-tomcat_imgserver-web | 1735 / imgserver-web（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 540.1 |
| 10.179.2.30 | 13445 | pss-service-exec.jar | 1721 / pss-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 890.6 |
| 10.179.2.30 | 13494 | /opt/apache-tomcat_workflow-service | 1724 / workflow-service（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 891.4 |
| 10.179.2.30 | 13791 | liquidation-service-exec.jar | 1758 / liquidation-service | 业务应用候选 | -Xmx1024M -Xms1024M | 1461.3 |
| 10.179.2.30 | 14015 | /opt/apache-tomcat_smart-sso-server | 1458 / smart-sso-server（推断） | 业务应用候选 | -Xmx3172M -Xms2048M -Xss256k | 1029.7 |
| 10.179.2.30 | 14369 | integration-client-exec.jar | 1714 / integration-client | 业务应用候选 | -Xmx512M -Xms512M | 523.3 |
| 10.179.2.30 | 14558 | train-hubs-ztrip-exec.jar | 1713 / train-hubs-ztrip | 业务应用候选 | -Xmx512M -Xms512M | 434.4 |
| 10.179.2.30 | 14709 | es-service-exec.jar | 1742 / es-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 843 |
| 10.179.2.30 | 1478 | intl-fbp-exec.jar | 1691 / intl-fbp（推断） | 业务应用候选 | -Xmx2048M -Xms2048M | 1029.6 |
| 10.179.2.30 | 14836 | /opt/apache-tomcat_passport-web | 1701 / passport-web（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 1029.4 |
| 10.179.2.30 | 15191 | /opt/apache-tomcat_expense-service | 1699 / expense-service（推断） | 业务应用候选 | -Xmx3172M -Xms2048M -Xss256k | 1234.3 |
| 10.179.2.30 | 15332 | mice-group-web-exec.jar | 1765 / mice-group-web（推断） | 业务应用候选 | -Xmx512M -Xms512M | 513 |
| 10.179.2.30 | 15992 | pbp-service-exec.jar | 1720 / pbp-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 903.6 |
| 10.179.2.30 | 16570 | hse-service-exec.jar | 1745 / hse-service（推断） | 业务应用候选 | -Xmx1024M -Xms512M | 1044 |
| 10.179.2.30 | 16819 | /opt/apache-tomcat_silkapi | 1618 / silkapi（推断） | 业务应用候选 | -Xms4G -Xmx4G | 4641.9 |
| 10.179.2.30 | 17566 | /opt/apache-tomcat_hbp-service | 1698 / hbp-service（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 845 |
| 10.179.2.30 | 18013 | train-web-exec.jar | 1729 / train-web | 业务应用候选 | -Xmx512M -Xms512M | 532.4 |
| 10.179.2.30 | 18332 | /opt/apache-tomcat_platform-openapi | 1695 / platform-openapi（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 1467.5 |
| 10.179.2.30 | 19908 | /opt/apache-tomcat_member-web-api | 1744 / member-web-api（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 816.8 |
| 10.179.2.30 | 22771 | job-service-exec.jar | 1858 / job-service | 业务应用候选 | -Xmx512M -Xms512M | 836.6 |
| 10.179.2.30 | 23371 | /opt/apache-tomcat_bi-service | 1702 / bi-service（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 1221.9 |
| 10.179.2.30 | 23859 | /opt/apache-tomcat_oms-service | 1738 / oms-service（推断） | 业务应用候选 | -Xmx2048M -Xms2048M | 1188 |
| 10.179.2.30 | 24007 | poi-service-exec.jar | 1692 / poi-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 536.5 |
| 10.179.2.30 | 24471 | payment-web-exec.jar | 1754 / payment-web | 业务应用候选 | -Xmx512M -Xms512M | 564.4 |
| 10.179.2.30 | 2519 | gbp-service-exec.jar | 1711 / gbp-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 641.1 |
| 10.179.2.30 | 25362 | pss-web-exec.jar | 1731 / pss-web | 业务应用候选 | -Xmx512M -Xms512M | 900.1 |
| 10.179.2.30 | 26708 | hotel-admin-web-exec.jar | 1806 / hotel-admin-web | 业务应用候选 | -Xmx512M -Xms512M | 1009.4 |
| 10.179.2.30 | 26935 | search-service-exec.jar | 1728 / search-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 545.2 |
| 10.179.2.30 | 27491 | /opt/apache-tomcat_sso-service | 1700 / sso-service（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 636.6 |
| 10.179.2.30 | 28094 | integration-fosun-exec.jar | 1716 / integration-fosun | 业务应用候选 | -Xmx512M -Xms512M | 387.8 |
| 10.179.2.30 | 28664 | member-service-exec.jar | 1688 / member-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 835.4 |
| 10.179.2.30 | 29414 | flight-web-api-exec.jar | 1726 / flight-web-api | 业务应用候选 | -Xmx4096M -Xms4096M | 1783.3 |
| 10.179.2.30 | 29751 | /opt/apache-tomcat_hotel-hubs-ztrip | 1752 / hotel-hubs-ztrip（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 832.3 |
| 10.179.2.30 | 30370 | flight-hubs-ztrip-service-exec.jar | 1727 / flight-hubs-ztrip-service（推断） | 业务应用候选 | -Xmx2048M -Xms2048M | 1121.2 |
| 10.179.2.30 | 30412 | /opt/apache-tomcat_tem-h5 | 1751 / tem-h5（推断） | 业务应用候选 | -Xmx4096M -Xms2048M | 2171.1 |
| 10.179.2.30 | 31009 | train-admin-web-exec.jar | 1802 / train-admin-web | 业务应用候选 | -Xmx512M -Xms512M | 572.1 |
| 10.179.2.30 | 348 | /opt/apache-tomcat_flight-admin-web | 1805 / flight-admin-web（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 691.5 |
| 10.179.2.30 | 3570 | /opt/apache-tomcat_integration-service | 1696 / integration-service（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 1208.7 |
| 10.179.2.30 | 4112 | flight-order-service-exec.jar | 1737 / flight-order-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 765.1 |
| 10.179.2.30 | 4485 | hotel-web-api-exec.jar | 1773 / hotel-web-api | 业务应用候选 | -Xmx1024M -Xms1024M | 784.5 |
| 10.179.2.30 | 4779 | /opt/apache-tomcat_platform-web | 1770 / platform-web（推断） | 业务应用候选 | -Xmx4096M -Xms4096M | 1813.1 |
| 10.179.2.30 | 5111 | tem-callback-exec.jar | 1710 / tem-callback | 业务应用候选 | -Xmx512M -Xms512M | 446.2 |
| 10.179.2.30 | 5150 | car-web-exec.jar | 1740 / car-web | 业务应用候选 | -Xmx512M -Xms512M | 600.2 |
| 10.179.2.30 | 6941 | fosun-service-exec.jar | 1739 / fosun-service | 业务应用候选 | -Xmx512M -Xms512M | 715.6 |
| 10.179.2.30 | 734 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2048m -Xmx3072m -Xmn1024m -Xss256k | 887.1 |
| 10.179.2.30 | 785 | /opt/apache-tomcat_platform-service | 1689 / platform-service（推断） | 业务应用候选 | -Xmx4096M -Xms4096M | 4724.5 |
| 10.179.2.30 | 8338 | domestic-fbp-service-exec.jar | 1725 / domestic-fbp-service（推断） | 业务应用候选 | -Xmx512M -Xms512M | 695.5 |
| 10.179.2.30 | 8638 | hbp-datasync-ztrip-exec.jar | 1747 / hbp-datasync-ztrip（推断） | 业务应用候选 | -Xmx512M -Xms512M | 488 |
| 10.179.2.30 | 9224 | /opt/apache-tomcat_tem-web | 1733 / tem-web（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 1491.8 |
| 10.179.2.30 | 9814 | /opt/apache-tomcat_notify-service | 1715 / notify-service（推断） | 业务应用候选 | -Xmx1024M -Xms1024M | 1033.2 |
| 10.179.2.9 | 28772 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms3g -Xmx3g -Xss1m -XX:MaxDirectMemorySize=1610612736 | 3583.5 |
| 10.179.2.9 | 516 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms1g -Xmx1g | 1460.8 |
| 10.179.3.14 | 10270 | ZooKeeper | 待核实 / ZooKeeper（启动命令识别的组件） | 待确认进程 | -Xmx1024m | 218.6 |
| 10.179.3.14 | 1141615 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx1536m | 980.5 |
| 10.179.3.14 | 159824 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx1024m -Xms1024m | 1326 |
| 10.179.3.14 | 196739 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms4096M -Xmx4096M -XX:MaxNewSize=128M | 3239.2 |
| 10.179.3.14 | 198227 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms4096M -Xmx4096M -XX:MaxNewSize=128M | 446.1 |
| 10.179.3.14 | 199196 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2048M -Xmx2048M -XX:MaxNewSize=138M | 507.8 |
| 10.179.3.14 | 218903 | 神策数据 CloudAtlas / SCA | 待核实 / 神策数据 CloudAtlas / SCA（启动命令识别的组件） | 待确认进程 | -Xmx768m | 1196.4 |
| 10.179.3.14 | 233035 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms256m -Xmx768m | 784.2 |
| 10.179.3.14 | 258530 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms512m -Xmx512m | 525.1 |
| 10.179.3.14 | 278910 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms128m -Xmx2047m | 462.7 |
| 10.179.3.14 | 318678 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx576m | 1125.7 |
| 10.179.3.14 | 389112 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms256m -Xmx513m | 1436.2 |
| 10.179.3.14 | 400823 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms64m -Xmx1024m -Xss512k | 1526 |
| 10.179.3.14 | 411792 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx487m | 1202.6 |
| 10.179.3.14 | 416920 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms256m -Xss512k -Xmx1536m | 2053.8 |
| 10.179.3.14 | 425216 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx977m | 1665.7 |
| 10.179.3.14 | 442166 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx128m -Xms128m | 389.4 |
| 10.179.3.14 | 528015 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx1271m -Xmn806m | 2258.9 |
| 10.179.3.14 | 536778 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx512m | 842.2 |
| 10.179.3.14 | 541317 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx512m | 924.4 |
| 10.179.3.14 | 544335 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx512m | 593.3 |
| 10.179.3.14 | 627812 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms256m -Xmx512m -Xmn256m | 788 |
| 10.179.3.14 | 7382 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms128m -Xmx1024m -Xss512k | 367.6 |
| 10.179.4.104 | 22601 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xss1m -Xms7942m -Xmx7942m -XX:MaxDirectMemorySize=4164943872 | 9278.1 |
| 10.179.4.104 | 482 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms1g -Xmx1g | 1657.8 |
| 10.179.4.108 | 25038 | discovery-exec.jar | 1462 / discovery | 业务应用候选 | -Xmx1G -Xms1G | 1444 |
| 10.179.4.108 | 8035 | merchant-processor-exec.jar | 1901 / merchant-processor | 业务应用候选 | -Xmx4G -Xms4G | 4567.8 |
| 10.179.4.12 | 28017 | coupon-manage-job-exec.jar | 1880 / coupon-manage-job | 业务应用候选 | -Xmx1G -Xms1G | 1579.6 |
| 10.179.4.123 | 1166 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms4g -Xmx4g -Xss1m -XX:MaxDirectMemorySize=2147483648 | 4723.5 |
| 10.179.4.123 | 1940 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xss1m -Xms4g -Xmx4g -XX:MaxDirectMemorySize=2147483648 | 4607.9 |
| 10.179.4.137 | 25558 | /opt/tomcat | 待核实 / 待核实 | 待确认进程 | -Xms3072M -Xmx3072M | 1523.7 |
| 10.179.4.137 | 8341 | datagear-4.6.0.jar | 待核实 / 待核实 | 待确认进程 | 未显式声明，JVM 生效值未核实 | 1049.8 |
| 10.179.4.139 | 5674 | /opt/apache-tomcat_dolphin-open | 1411 / dolphin-open（推断） | 业务应用候选 | -Xms4G -Xmx4G | 2967 |
| 10.179.4.148 | 27854 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2048m -Xmx3072m | 1548.2 |
| 10.179.4.148 | 28118 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2048m -Xmx2048m -Xmn1024m -Xss256k | 802.8 |
| 10.179.4.154 | 8344 | short-links-web-exec.jar | 1666 / short-links-web | 业务应用候选 | -Xmx2G -Xms1G | 1996.9 |
| 10.179.4.155 | 10190 | live-center-service-exec.jar | 1861 / live-center-service | 业务应用候选 | -Xmx2G -Xms1G | 2349.4 |
| 10.179.4.155 | 30273 | point-processor-exec.jar | 1892 / point-processor | 业务应用候选 | -Xmx3G -Xms2G | 1922.7 |
| 10.179.4.156 | 7455 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms8g -Xmx8g -Xss1m -XX:MaxDirectMemorySize=4294967296 | 9347.4 |
| 10.179.4.161 | 19692 | cls-api-job-exec.jar | 1876 / cls-api-job | 业务应用候选 | -Xmx2G -Xms2G | 2646.2 |
| 10.179.4.162 | 20140 | /opt/apache-tomcat_dolphin-switches | 1412 / dolphin-switches（推断） | 业务应用候选 | -Xms13G -Xmx13G | 6498.8 |
| 10.179.4.164 | 31799 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms1g -Xmx1g -Xss1m | 1791.2 |
| 10.179.4.165 | 14443 | foliday-photo-opeadmin-exec.jar | 1490 / foliday-photo-opeadmin | 业务应用候选 | -Xmx1G -Xms1G | 1418.8 |
| 10.179.4.165 | 19065 | photo-standard-job-exec.jar | 1504 / photo-standard-job | 业务应用候选 | -Xmx3G -Xms2G | 1768.2 |
| 10.179.4.165 | 2566 | foliday-photo-sync-exec.jar | 1500 / foliday-photo-sync | 业务应用候选 | -Xmx1G -Xms1G | 790.4 |
| 10.179.4.165 | 3319 | foliday-photo-web-exec.jar | 1497 / foliday-photo-web | 业务应用候选 | -Xmx1G -Xms1G | 1494.7 |
| 10.179.4.179 | 16759 | alipaymini-task-exec.jar | 1602 / alipaymini-task | 业务应用候选 | -Xmx1G -Xms1G | 1702.4 |
| 10.179.4.179 | 18389 | fcrm-job-exec.jar | 1882 / fcrm-job | 业务应用候选 | -Xmx2G -Xms2G | 1735 |
| 10.179.4.179 | 19141 | mebc-point-processor-exec.jar | 1898 / mebc-point-processor | 业务应用候选 | -Xmx2G -Xms1G | 2518.3 |
| 10.179.4.179 | 21004 | mebc-growth-processor-exec.jar | 1897 / mebc-growth-processor | 业务应用候选 | -Xmx3G -Xms3G | 2799.1 |
| 10.179.4.179 | 2239 | mebc-order-processor-exec.jar | 1896 / mebc-order-processor | 业务应用候选 | -Xmx2G -Xms1G | 2570.7 |
| 10.179.4.179 | 7531 | mebc-meb-processor-exec.jar | 1899 / mebc-meb-processor | 业务应用候选 | -Xmx2G -Xms1G | 2258.2 |
| 10.179.4.186 | 17480 | -Dspring.profiles.active=prod | 待核实 / 待核实 | 待确认进程 | -Xms2G -Xmx2G | 2429.4 |
| 10.179.4.187 | 796 | -Dspring.profiles.active=prod | 待核实 / 待核实 | 待确认进程 | -Xms2G -Xmx2G | 1222.1 |
| 10.179.4.188 | 21299 | cooperate-processor-exec.jar | 1889 / cooperate-processor | 业务应用候选 | -Xmx1G -Xms1G | 1763.4 |
| 10.179.4.189 | 16171 | -Dspring.profiles.active=prod | 待核实 / 待核实 | 待确认进程 | -Xms3072M -Xmx3072M | 1825.6 |
| 10.179.4.192 | 3105 | /opt/apache-tomcat_dolphin-switches | 1412 / dolphin-switches（推断） | 业务应用候选 | -Xms11G -Xmx11G | 6127.5 |
| 10.179.4.193 | 19758 | /opt/apache-tomcat_dolphin-switches | 1412 / dolphin-switches（推断） | 业务应用候选 | -Xms11G -Xmx11G | 6104.3 |
| 10.179.4.194 | 22506 | mebc-meb-processor-exec.jar | 1899 / mebc-meb-processor | 业务应用候选 | -Xmx2G -Xms1G | 1318.5 |
| 10.179.4.194 | 4710 | fcrm-job-exec.jar | 1882 / fcrm-job | 业务应用候选 | -Xmx2G -Xms2G | 1509.5 |
| 10.179.4.196 | 22685 | /opt/apache-tomcat_dolphin-open | 1411 / dolphin-open（推断） | 业务应用候选 | -Xms10G -Xmx10G | 4701 |
| 10.179.4.203 | 14610 | marketing-equity-offline-service-exec.jar | 1800 / marketing-equity-offline-service | 业务应用候选 | -Xmx3G -Xms2G | 1791.3 |
| 10.179.4.203 | 4457 | active-marketing-offline-exec.jar | 1616 / active-marketing-offline | 业务应用候选 | -Xmx2G -Xms1G | 2441.6 |
| 10.179.4.203 | 6660 | marketing-center-activity-backend-service-exec.jar | 1381 / marketing-center-activity-backend-service | 业务应用候选 | -Xmx2G -Xms1G | 1819.4 |
| 10.179.4.204 | 18386 | uc-facade-exec.jar | 1431 / uc-facade | 业务应用候选 | -Xmx4G -Xms4G | 2347.2 |
| 10.179.4.204 | 24796 | crm-markting-notify-api-job-exec.jar | 1881 / crm-markting-notify-api-job | 业务应用候选 | -Xmx1G -Xms1G | 1108 |
| 10.179.4.204 | 4228 | product-activity-offline-exec.jar | 1894 / product-activity-offline | 业务应用候选 | -Xmx2G -Xms1G | 2334.3 |
| 10.179.4.206 | 30337 | live-center-service-exec.jar | 1861 / live-center-service | 业务应用候选 | -Xmx2G -Xms1G | 1601.4 |
| 10.179.4.206 | 9428 | /opt/tomcat | 待核实 / 待核实 | 待确认进程 | -Xms1G -Xmx3072M | 866.3 |
| 10.179.4.215 | 1955 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms16g -Xmx16g -Xss1m -XX:MaxDirectMemorySize=8589934592 | 18493.2 |
| 10.179.4.215 | 3520 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms4g -Xmx4g | 4702 |
| 10.179.4.216 | 18675 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms8g -Xmx8g -Xss1m -XX:MaxDirectMemorySize=4294967296 | 9536.5 |
| 10.179.4.217 | 29233 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms4g -Xmx4g -Xss1m -XX:MaxDirectMemorySize=2147483648 | 4939.7 |
| 10.179.4.226 | 31918 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms4g -Xmx4g -Xss1m | 4674.7 |
| 10.179.4.227 | 12809 | nacos-server.jar | 待核实 / 待核实 | 待确认进程 | -Xms2g -Xmx2g -Xmn1g -XX:MaxMetaspaceSize=320m | 2590.3 |
| 10.179.4.238 | 2205 | short-links-web-exec.jar | 1666 / short-links-web | 业务应用候选 | -Xmx2G -Xms2G | 1026.5 |
| 10.179.4.241 | 31649 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms4g -Xmx4g -Xss1m -XX:MaxDirectMemorySize=2147483648 | 5011.3 |
| 10.179.4.245 | 27002 | /opt/tomcat | 待核实 / 待核实 | 待确认进程 | -Xms2048M -Xmx2048M | 1630.1 |
| 10.179.4.247 | 13124 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms16g -Xmx16g -Xss1m -XX:MaxDirectMemorySize=8589934592 | 17950.3 |
| 10.179.4.247 | 25310 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms4g -Xmx4g | 4679.8 |
| 10.179.4.250 | 20303 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms2g -Xmx2g -Xss1m -XX:MaxDirectMemorySize=1073741824 | 2376.8 |
| 10.179.4.250 | 20456 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms2g -Xmx2g -Xss1m -XX:MaxDirectMemorySize=1073741824 | 2419.3 |
| 10.179.4.251 | 1256 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms1024m -Xmx1024m | 995.8 |
| 10.179.4.251 | 3190 | discovery-exec.jar | 1462 / discovery | 业务应用候选 | -Xmx1G -Xms1G | 904.3 |
| 10.179.4.251 | 32736 | questionnaire-survey-exec.jar | 1455 / questionnaire-survey | 业务应用候选 | -Xmx3G -Xms2G | 1222.4 |
| 10.179.4.252 | 17525 | ZooKeeper | 待核实 / ZooKeeper（启动命令识别的组件） | 待确认进程 | -Xmx512M -Xms512M | 367.6 |
| 10.179.4.252 | 21880 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx4G -Xms4G | 3743.2 |
| 10.179.4.47 | 16584 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2048m -Xmx3072m -Xmn1024m -Xss256k | 949.8 |
| 10.179.4.47 | 25369 | rundeck-3.4.3-20210823.war | 待核实 / 待核实 | 待确认进程 | 未显式声明，JVM 生效值未核实 | 1745.2 |
| 10.179.4.47 | 27177 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx2048m | 333.1 |
| 10.179.4.47 | 31534 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms2g -Xmx2g -Xmn1g -XX:MaxMetaspaceSize=128m -Xss512k | 2023.5 |
| 10.179.4.47 | 31640 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms1g -Xmx1g -Xmn512m -XX:MaxMetaspaceSize=128m -Xss512k | 754.2 |
| 10.179.4.47 | 31737 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms512m -Xmx512m -Xmn256m -XX:MaxMetaspaceSize=128m -Xss512k | 187.4 |
| 10.179.4.64 | 16579 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xmx2G -Xms2G | 1348.5 |
| 10.179.4.72 | 13586 | photo-standard-job-exec.jar | 1504 / photo-standard-job | 业务应用候选 | -Xmx3G -Xms2G | 1784 |
| 10.179.4.72 | 14939 | foliday-photo-sync-exec.jar | 1500 / foliday-photo-sync | 业务应用候选 | -Xmx1G -Xms1G | 754.8 |
| 10.179.4.72 | 17280 | foliday-photo-web-exec.jar | 1497 / foliday-photo-web | 业务应用候选 | -Xmx1G -Xms1G | 1514.3 |
| 10.179.4.72 | 17982 | foliday-photo-opeadmin-exec.jar | 1490 / foliday-photo-opeadmin | 业务应用候选 | -Xmx1G -Xms1G | 1418.4 |
| 10.179.4.79 | 3344 | user-account-center-admin-exec.jar | 1762 / user-account-center-admin | 业务应用候选 | -Xmx3G -Xms2G | 1245.1 |
| 10.179.4.79 | 5496 | spark-api-exec.jar | 1496 / spark-api | 业务应用候选 | -Xmx3G -Xms2G | 3313.2 |
| 10.179.4.79 | 6081 | spark-admin-exec.jar | 1512 / spark-admin | 业务应用候选 | -Xmx3G -Xms2G | 3104 |
| 10.179.4.80 | 11262 | spark-api-exec.jar | 1496 / spark-api | 业务应用候选 | -Xmx3G -Xms2G | 3536.1 |
| 10.179.4.80 | 12263 | spark-admin-exec.jar | 1512 / spark-admin | 业务应用候选 | -Xmx3G -Xms2G | 4107.3 |
| 10.179.4.80 | 4844 | user-account-center-admin-exec.jar | 1762 / user-account-center-admin | 业务应用候选 | -Xmx3G -Xms2G | 1254.4 |
| 10.179.4.81 | 20641 | merchant-processor-exec.jar | 1901 / merchant-processor | 业务应用候选 | -Xmx4G -Xms4G | 2072.8 |
| 10.179.4.91 | 6266 | merchant-processor-exec.jar | 1901 / merchant-processor | 业务应用候选 | -Xmx4G -Xms4G | 2174.8 |
| 10.179.5.145 | 1030 | /opt/apache-tomcat_dubbo-adaptor-web | 1571 / dubbo-adaptor-web（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1753.5 |
| 10.179.5.145 | 10968 | /opt/apache-tomcat_fostay-online-product | 1396 / fostay-online-product（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1443.5 |
| 10.179.5.145 | 11341 | fcrs-base-exec.jar | 1422 / fcrs-base | 业务应用候选 | -Xmx3G -Xms2G | 3256.7 |
| 10.179.5.145 | 11751 | /opt/apache-tomcat_fostay-online-order | 1395 / fostay-online-order（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 3485.4 |
| 10.179.5.145 | 12518 | fmsg-web-exec.jar | 1477 / fmsg-web | 业务应用候选 | -Xmx3G -Xms2G | 3752.4 |
| 10.179.5.145 | 14983 | /opt/apache-tomcat_fostay-sys-order | 1398 / fostay-sys-order（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 3562.6 |
| 10.179.5.145 | 16290 | /opt/apache-tomcat_phoenix-online-order | 1573 / phoenix-online-order（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2968.1 |
| 10.179.5.145 | 17896 | /opt/apache-tomcat_hotel-ari-api | 1553 / hotel-ari-api（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1216.4 |
| 10.179.5.145 | 20245 | /opt/apache-tomcat_phoenix-fliggyapi | 1531 / phoenix-fliggyapi（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1577.9 |
| 10.179.5.145 | 21435 | /opt/apache-tomcat_phoenix-online-product | 1560 / phoenix-online-product（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1418.1 |
| 10.179.5.145 | 22247 | /opt/apache-tomcat_hotel-ari-task | 1510 / hotel-ari-task（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2847.8 |
| 10.179.5.145 | 22538 | fcrs-channel-exec.jar | 1423 / fcrs-channel | 业务应用候选 | -Xmx3G -Xms2G | 1412.2 |
| 10.179.5.145 | 23944 | /opt/apache-tomcat_phoenix-mappingtool | 1656 / phoenix-mappingtool（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2009.6 |
| 10.179.5.145 | 25624 | fcrs-fpms-exec.jar | 1424 / fcrs-fpms | 业务应用候选 | -Xmx3G -Xms2G | 3323.5 |
| 10.179.5.145 | 26785 | /opt/apache-tomcat_hotel-ari-abut | 1509 / hotel-ari-abut（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2492.1 |
| 10.179.5.145 | 27045 | fcrs-product-pump-exec.jar | 1425 / fcrs-product-pump | 业务应用候选 | -Xmx3G -Xms2G | 2779 |
| 10.179.5.145 | 28371 | /opt/apache-tomcat_fostay-sys-rules | 1443 / fostay-sys-rules（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1835.3 |
| 10.179.5.145 | 28869 | /opt/apache-tomcat_fostay-sys-foticket | 1399 / fostay-sys-foticket（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1630.5 |
| 10.179.5.145 | 30821 | ctrip-api-exec.jar | 1646 / ctrip-api | 业务应用候选 | -Xmx3G -Xms2G | 3764.6 |
| 10.179.5.145 | 31067 | /opt/apache-tomcat_hotel-cms | 1495 / hotel-cms（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1524.7 |
| 10.179.5.145 | 31858 | fmsg-service-exec.jar | 1476 / fmsg-service | 业务应用候选 | -Xmx3G -Xms2G | 3606.3 |
| 10.179.5.145 | 3403 | /opt/apache-tomcat_phoenix-open-web | 1874 / phoenix-open-web（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1376.3 |
| 10.179.5.145 | 3897 | /opt/apache-tomcat_dolphin-webhook | 1441 / dolphin-webhook（推断） | 业务应用候选 | -Xms4G -Xmx4G | 4869.8 |
| 10.179.5.145 | 5091 | /opt/apache-tomcat_dolphin-task | 1483 / dolphin-task（推断） | 业务应用候选 | -Xms4G -Xmx4G | 2002.5 |
| 10.179.5.145 | 5524 | /opt/apache-tomcat_fostay-sys-preprocess | 1400 / fostay-sys-preprocess（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 3673.8 |
| 10.179.5.145 | 5611 | /opt/apache-tomcat_fostay-sys | 1397 / fostay-sys（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1807.4 |
| 10.179.5.145 | 7034 | /opt/apache-tomcat_dolphin-mq | 1521 / dolphin-mq（推断） | 业务应用候选 | -Xms4G -Xmx4G | 4588.2 |
| 10.179.5.145 | 8757 | /opt/apache-tomcat_dolphin-task-service | 1452 / dolphin-task-service（推断） | 业务应用候选 | -Xms4G -Xmx4G | 2166.3 |
| 10.179.5.146 | 1070 | /opt/apache-tomcat_tims-finance-web | 1552 / tims-finance-web（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1376.6 |
| 10.179.5.146 | 1240 | /opt/apache-tomcat_foliday-mms | 1851 / foliday-mms（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1214 |
| 10.179.5.146 | 15919 | /opt/apache-tomcat_log-service | 1617 / log-service（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2321 |
| 10.179.5.146 | 16141 | fosun-aggregation-openapi-exec.jar | 1911 / fosun-aggregation-openapi | 业务应用候选 | -Xmx3G -Xms2G | 1304.1 |
| 10.179.5.146 | 17613 | public-online-exec.jar | 1663 / public-online | 业务应用候选 | -Xmx2G -Xms2G | 1752.1 |
| 10.179.5.146 | 19548 | fosun-payment-exec.jar | 1535 / fosun-payment | 业务应用候选 | -Xmx3G -Xms2G | 1449.9 |
| 10.179.5.146 | 19817 | fosun-merchant-exec.jar | 1368 / fosun-merchant | 业务应用候选 | -Xmx3G -Xms2G | 2791.2 |
| 10.179.5.146 | 20271 | fosun-settlement-exec.jar | 1446 / fosun-settlement | 业务应用候选 | -Xmx3G -Xms2G | 3434.5 |
| 10.179.5.146 | 24221 | fosun-aggregation-facade-exec.jar | 1910 / fosun-aggregation-facade | 业务应用候选 | -Xmx3G -Xms2G | 1711.7 |
| 10.179.5.146 | 2463 | message-center-admin-exec.jar | 1516 / message-center-admin | 业务应用候选 | -Xmx3G -Xms2G | 1423.6 |
| 10.179.5.146 | 27478 | /opt/apache-tomcat_foliday-sms | 1534 / foliday-sms（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1472.7 |
| 10.179.5.146 | 29139 | message-center-consumer-exec.jar | 1518 / message-center-consumer | 业务应用候选 | -Xmx3G -Xms2G | 3784.6 |
| 10.179.5.146 | 29166 | message-center-api-exec.jar | 1517 / message-center-api | 业务应用候选 | -Xmx3G -Xms2G | 2860.8 |
| 10.179.5.146 | 30303 | base-api-exec.jar | 1373 / base-api | 业务应用候选 | -Xmx4G -Xms4G | 3087.3 |
| 10.179.5.146 | 31597 | buyer-api-exec.jar | 1372 / buyer-api | 业务应用候选 | -Xmx2G -Xms2G | 2566.1 |
| 10.179.5.146 | 31904 | mbp-seller-api-exec.jar | 1378 / mbp-seller-api | 业务应用候选 | -Xmx4G -Xms2G -XX:MaxMetaspaceSize=256m | 2477 |
| 10.179.5.146 | 32178 | cashier-api-exec.jar | 1374 / cashier-api | 业务应用候选 | -Xmx4G -Xms4G | 3050.3 |
| 10.179.5.146 | 32245 | fosun-cashier-exec.jar | 1447 / fosun-cashier | 业务应用候选 | -Xmx3G -Xms2G | 3014.6 |
| 10.179.5.146 | 32334 | manager-api-exec.jar | 1377 / manager-api | 业务应用候选 | -Xmx4G -Xms4G | 3031.8 |
| 10.179.5.146 | 6161 | /opt/apache-tomcat_security-web | 709 / security-web（推断） | 业务应用候选 | -Xmx3172M -Xms1024M -Xss256k | 1351.8 |
| 10.179.5.146 | 6849 | fomall-gateway-exec.jar | 1440 / fomall-gateway | 业务应用候选 | -Xmx2G -Xms2G | 1342.3 |
| 10.179.5.146 | 6980 | consumer-exec.jar | 1376 / consumer | 业务应用候选 | -Xmx4G -Xms4G | 3491.3 |
| 10.179.5.146 | 7539 | /opt/apache-tomcat_online-search | 1428 / online-search（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1755.6 |
| 10.179.5.146 | 8264 | finance-check-exec.jar | 1533 / finance-check | 业务应用候选 | -Xmx3G -Xms2G | 1837.9 |
| 10.179.5.146 | 9622 | /opt/apache-tomcat_fothing-payment | 1438 / fothing-payment（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2064.2 |
| 10.179.5.146 | 9936 | /opt/apache-tomcat_fsop-productcenter | 1427 / fsop-productcenter（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 3657 |
| 10.179.5.147 | 11117 | /opt/apache-tomcat_phoenix-open-web | 1874 / phoenix-open-web（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1286.5 |
| 10.179.5.147 | 1258 | fcrs-base-exec.jar | 1422 / fcrs-base | 业务应用候选 | -Xmx3G -Xms2G | 3764.5 |
| 10.179.5.147 | 12972 | fosun-aggregation-openapi-exec.jar | 1911 / fosun-aggregation-openapi | 业务应用候选 | -Xmx3G -Xms2G | 1311.5 |
| 10.179.5.147 | 14158 | /opt/apache-tomcat_fostay-online-order | 1395 / fostay-online-order（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 3491 |
| 10.179.5.147 | 17413 | /opt/apache-tomcat_fostay-online-product | 1396 / fostay-online-product（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1448.4 |
| 10.179.5.147 | 1854 | fcrs-channel-exec.jar | 1423 / fcrs-channel | 业务应用候选 | -Xmx3G -Xms2G | 1410.5 |
| 10.179.5.147 | 21739 | /opt/apache-tomcat_hotel-ari-task | 1510 / hotel-ari-task（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1512.5 |
| 10.179.5.147 | 22376 | /opt/apache-tomcat_phoenix-mappingtool | 1656 / phoenix-mappingtool（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1996 |
| 10.179.5.147 | 22773 | fmsg-web-exec.jar | 1477 / fmsg-web | 业务应用候选 | -Xmx3G -Xms2G | 3544.5 |
| 10.179.5.147 | 23377 | /opt/apache-tomcat_phoenix-online-product | 1560 / phoenix-online-product（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1390.5 |
| 10.179.5.147 | 24754 | /opt/apache-tomcat_phoenix-fliggyapi | 1531 / phoenix-fliggyapi（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1589.2 |
| 10.179.5.147 | 2486 | fcrs-fpms-exec.jar | 1424 / fcrs-fpms | 业务应用候选 | -Xmx3G -Xms2G | 3333.4 |
| 10.179.5.147 | 25813 | /opt/apache-tomcat_hotel-cms | 1495 / hotel-cms（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1540.4 |
| 10.179.5.147 | 26359 | /opt/apache-tomcat_dolphin-mq | 1521 / dolphin-mq（推断） | 业务应用候选 | -Xms4G -Xmx4G | 2110.2 |
| 10.179.5.147 | 26938 | fosun-aggregation-facade-exec.jar | 1910 / fosun-aggregation-facade | 业务应用候选 | -Xmx3G -Xms2G | 1743.9 |
| 10.179.5.147 | 28576 | /opt/apache-tomcat_dubbo-adaptor-web | 1571 / dubbo-adaptor-web（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1633.6 |
| 10.179.5.147 | 29142 | ctrip-api-exec.jar | 1646 / ctrip-api | 业务应用候选 | -Xmx3G -Xms2G | 1597 |
| 10.179.5.147 | 3944 | fcrs-product-pump-exec.jar | 1425 / fcrs-product-pump | 业务应用候选 | -Xmx3G -Xms2G | 2577.9 |
| 10.179.5.147 | 4329 | hotel-joy-backend-exec.jar | 1849 / hotel-joy-backend | 业务应用候选 | -Xmx3G -Xms2G | 1654.1 |
| 10.179.5.147 | 4499 | fmsg-service-exec.jar | 1476 / fmsg-service | 业务应用候选 | -Xmx3G -Xms2G | 3647.5 |
| 10.179.5.147 | 5153 | /opt/apache-tomcat_dolphin-task-service | 1452 / dolphin-task-service（推断） | 业务应用候选 | -Xms4G -Xmx4G | 2165.6 |
| 10.179.5.147 | 5241 | /opt/apache-tomcat_dolphin-task | 1483 / dolphin-task（推断） | 业务应用候选 | -Xms4G -Xmx4G | 2155.9 |
| 10.179.5.147 | 6882 | /opt/apache-tomcat_dolphin-webhook | 1441 / dolphin-webhook（推断） | 业务应用候选 | -Xms4G -Xmx4G | 4946.3 |
| 10.179.5.147 | 7822 | /opt/apache-tomcat_hotel-ari-abut | 1509 / hotel-ari-abut（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2633.1 |
| 10.179.5.147 | 9139 | /opt/apache-tomcat_hotel-ari-api | 1553 / hotel-ari-api（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1219.1 |
| 10.179.5.147 | 9712 | /opt/apache-tomcat_phoenix-online-order | 1573 / phoenix-online-order（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2967.7 |
| 10.179.5.148 | 1027 | fosun-settlement-exec.jar | 1446 / fosun-settlement | 业务应用候选 | -Xmx3G -Xms2G | 2972.3 |
| 10.179.5.148 | 11085 | base-api-exec.jar | 1373 / base-api | 业务应用候选 | -Xmx4G -Xms4G | 3081.9 |
| 10.179.5.148 | 12968 | message-center-api-exec.jar | 1517 / message-center-api | 业务应用候选 | -Xmx3G -Xms2G | 2974.8 |
| 10.179.5.148 | 13163 | message-center-consumer-exec.jar | 1518 / message-center-consumer | 业务应用候选 | -Xmx3G -Xms2G | 3719.8 |
| 10.179.5.148 | 13506 | /opt/apache-tomcat_online-search | 1428 / online-search（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1852.1 |
| 10.179.5.148 | 14954 | message-center-admin-exec.jar | 1516 / message-center-admin | 业务应用候选 | -Xmx3G -Xms2G | 1426.8 |
| 10.179.5.148 | 16042 | buyer-api-exec.jar | 1372 / buyer-api | 业务应用候选 | -Xmx2G -Xms2G | 2500 |
| 10.179.5.148 | 16661 | mbp-seller-api-exec.jar | 1378 / mbp-seller-api | 业务应用候选 | -Xmx4G -Xms2G | 1976.8 |
| 10.179.5.148 | 16971 | settlement-task-exec.jar | 1530 / settlement-task | 业务应用候选 | -Xmx3G -Xms2G | 3031.1 |
| 10.179.5.148 | 17188 | cashier-api-exec.jar | 1374 / cashier-api | 业务应用候选 | -Xmx4G -Xms4G | 3075.3 |
| 10.179.5.148 | 18765 | fosun-cashier-exec.jar | 1447 / fosun-cashier | 业务应用候选 | -Xmx3G -Xms2G | 2730.3 |
| 10.179.5.148 | 20188 | manager-api-exec.jar | 1377 / manager-api | 业务应用候选 | -Xmx4G -Xms4G | 3040.3 |
| 10.179.5.148 | 20658 | consumer-exec.jar | 1376 / consumer | 业务应用候选 | -Xmx4G -Xms4G | 3426.1 |
| 10.179.5.148 | 2158 | fosun-aggregation-openapi-exec.jar | 1911 / fosun-aggregation-openapi | 业务应用候选 | -Xmx3G -Xms2G | 1323.9 |
| 10.179.5.148 | 23923 | /opt/apache-tomcat_foliday-sms | 1534 / foliday-sms（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1365.8 |
| 10.179.5.148 | 24577 | /opt/apache-tomcat_log-service | 1617 / log-service（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2002.3 |
| 10.179.5.148 | 24748 | fosun-aggregation-processor-exec.jar | 1913 / fosun-aggregation-processor | 业务应用候选 | -Xmx3G -Xms2G | 1272.5 |
| 10.179.5.148 | 30494 | fomall-gateway-exec.jar | 1440 / fomall-gateway | 业务应用候选 | -Xmx2G -Xms2G | 1322.1 |
| 10.179.5.148 | 32361 | /opt/apache-tomcat_fsop-productcenter | 1427 / fsop-productcenter（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 3688.2 |
| 10.179.5.148 | 32462 | /opt/apache-tomcat_fothing-payment | 1438 / fothing-payment（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1971.5 |
| 10.179.5.148 | 32704 | fosun-payment-exec.jar | 1535 / fosun-payment | 业务应用候选 | -Xmx3G -Xms2G | 1445.5 |
| 10.179.5.148 | 3432 | public-online-exec.jar | 1663 / public-online | 业务应用候选 | -Xmx2G -Xms2G | 1589.1 |
| 10.179.5.148 | 3599 | finance-check-exec.jar | 1533 / finance-check | 业务应用候选 | -Xmx3G -Xms2G | 1743.2 |
| 10.179.5.148 | 4684 | /opt/apache-tomcat_security-web | 709 / security-web（推断） | 业务应用候选 | -Xmx3172M -Xms1024M -Xss256k | 1355.8 |
| 10.179.5.148 | 525 | fosun-merchant-exec.jar | 1368 / fosun-merchant | 业务应用候选 | -Xmx3G -Xms2G | 2602.5 |
| 10.179.5.149 | 12052 | marketing-equity-front-service-exec.jar | 1804 / marketing-equity-front-service | 业务应用候选 | -Xmx2G -Xms1G | 2477.1 |
| 10.179.5.149 | 12105 | coupon-manage-exec.jar | 1465 / coupon-manage | 业务应用候选 | -Xmx3G -Xms2G | 3752.3 |
| 10.179.5.149 | 13096 | app-admin-exec.jar | 1434 / app-admin | 业务应用候选 | -Xmx2G -Xms1G | 1597.4 |
| 10.179.5.149 | 13143 | mebc-meb-service-exec.jar | 1669 / mebc-meb-service | 业务应用候选 | -Xmx3G -Xms2G | 2362.1 |
| 10.179.5.149 | 13422 | marketing-center-offline-exec.jar | 1380 / marketing-center-offline | 业务应用候选 | -Xmx4G -Xms2G | 2874.1 |
| 10.179.5.149 | 13531 | coupon-web-exec.jar | 1464 / coupon-web | 业务应用候选 | -Xmx3G -Xms2G | 3648.3 |
| 10.179.5.149 | 13806 | equity-manage-exec.jar | 1468 / equity-manage | 业务应用候选 | -Xmx3G -Xms2G | 2468.8 |
| 10.179.5.149 | 14004 | point-manage-exec.jar | 1472 / point-manage | 业务应用候选 | -Xmx2G -Xms2G | 1390.2 |
| 10.179.5.149 | 1411 | fcrm-consumer-exec.jar | 1470 / fcrm-consumer | 业务应用候选 | -Xmx3G -Xms2G | 2134.5 |
| 10.179.5.149 | 14581 | member-center-sync-exec.jar | 1405 / member-center-sync | 业务应用候选 | -Xmx2G -Xms2G | 2737.2 |
| 10.179.5.149 | 1789 | coupon-config-exec.jar | 1463 / coupon-config | 业务应用候选 | -Xmx3G -Xms2G | 1301 |
| 10.179.5.149 | 18126 | ups-api-exec.jar | 1475 / ups-api | 业务应用候选 | -Xmx2G -Xms2G | 2233.3 |
| 10.179.5.149 | 18677 | cooperate-union-service-exec.jar | 1598 / cooperate-union-service | 业务应用候选 | -Xmx1G -Xms1G | 1353.5 |
| 10.179.5.149 | 19011 | mebc-privilege-service-exec.jar | 1408 / mebc-privilege-service | 业务应用候选 | -Xmx1G -Xms1G | 1736.4 |
| 10.179.5.149 | 19545 | uc-online-exec.jar | 1432 / uc-online | 业务应用候选 | -Xmx1G -Xms1G | 1128 |
| 10.179.5.149 | 19848 | product-activity-service-exec.jar | 1672 / product-activity-service | 业务应用候选 | -Xmx2G -Xms1G | 2318.6 |
| 10.179.5.149 | 20170 | uc-admin-exec.jar | 1429 / uc-admin | 业务应用候选 | -Xmx1G -Xms1G | 1290.6 |
| 10.179.5.149 | 20292 | foliday-yue-exec.jar | 1456 / foliday-yue（推断） | 业务应用候选 | -Xmx2G -Xms2G | 1403.2 |
| 10.179.5.149 | 21215 | equity-web-exec.jar | 1469 / equity-web | 业务应用候选 | -Xmx2G -Xms2G | 1707.7 |
| 10.179.5.149 | 21367 | mebc-order-service-exec.jar | 1404 / mebc-order-service | 业务应用候选 | -Xmx2G -Xms2G | 2231.5 |
| 10.179.5.149 | 21475 | fcrm-exec.jar | 1453 / fcrm | 业务应用候选 | -Xmx3G -Xms2G | 3099.3 |
| 10.179.5.149 | 22485 | uc-oapi-exec.jar | 1430 / uc-oapi | 业务应用候选 | -Xmx1G -Xms1G | 1387.4 |
| 10.179.5.149 | 22562 | mebc-growth-service-exec.jar | 1406 / mebc-growth-service | 业务应用候选 | -Xmx2G -Xms2G | 2831.3 |
| 10.179.5.149 | 23962 | mebc-user-service-exec.jar | 1514 / mebc-user-service | 业务应用候选 | -Xmx2G -Xms2G | 1482.7 |
| 10.179.5.149 | 2533 | /opt/apache-tomcat_foliday_meb | 1460 / foliday_meb（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2594.6 |
| 10.179.5.149 | 2578 | /home/fosun/tomcat_mms_8184 | 待核实 / 待核实 | 待确认进程 | -Xms128m -Xmx1024m -Xmn80m -Xss256k | 852.3 |
| 10.179.5.149 | 27592 | member-center-offline-exec.jar | 1407 / member-center-offline | 业务应用候选 | -Xmx1G -Xms1G | 1705.2 |
| 10.179.5.149 | 2773 | live-center-offline-exec.jar | 1860 / live-center-offline | 业务应用候选 | -Xmx2G -Xms1G | 2303.3 |
| 10.179.5.149 | 28041 | crm-markting-notify-api-exec.jar | 1537 / crm-markting-notify-api | 业务应用候选 | -Xmx4G -Xms4G | 2194.3 |
| 10.179.5.149 | 28702 | bonussync-exec.jar | 1457 / bonussync | 业务应用候选 | -Xmx3G -Xms2G | 2053.6 |
| 10.179.5.149 | 29385 | app-api-exec.jar | 1433 / app-api | 业务应用候选 | -Xmx4G -Xms4G | 2695.1 |
| 10.179.5.149 | 30198 | alipaymini-admin-exec.jar | 1637 / alipaymini-admin | 业务应用候选 | -Xmx2G -Xms2G | 1858.8 |
| 10.179.5.149 | 30427 | alipaymini-processor-exec.jar | 1904 / alipaymini-processor | 业务应用候选 | -Xmx2G -Xms1G | 2676.7 |
| 10.179.5.149 | 32223 | point-web-exec.jar | 1471 / point-web | 业务应用候选 | -Xmx2G -Xms2G | 1368.2 |
| 10.179.5.149 | 3590 | mebc-point-service-exec.jar | 1839 / mebc-point-service | 业务应用候选 | -Xmx3G -Xms2G | 3489.8 |
| 10.179.5.149 | 4124 | coupon-consumer-exec.jar | 1466 / coupon-consumer | 业务应用候选 | -Xmx3G -Xms2G | 2122.2 |
| 10.179.5.149 | 4736 | alipaymini-web-exec.jar | 1565 / alipaymini-web | 业务应用候选 | -Xmx3G -Xms2G | 2064 |
| 10.179.5.149 | 6789 | active-marketing-service-exec.jar | 1650 / active-marketing-service | 业务应用候选 | -Xmx2G -Xms1G | 1646.1 |
| 10.179.5.149 | 7851 | /opt/apache-tomcat_smart-sso-server | 1458 / smart-sso-server（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1131.4 |
| 10.179.5.149 | 7856 | service-api-exec.jar | 1467 / service-api | 业务应用候选 | -Xmx3G -Xms2G | 1812 |
| 10.179.5.149 | 9060 | marketing-center-activity-front-service-exec.jar | 1382 / marketing-center-activity-front-service | 业务应用候选 | -Xmx4G -Xms2G | 2984.6 |
| 10.179.5.150 | 1039 | alipaymini-admin-exec.jar | 1637 / alipaymini-admin | 业务应用候选 | -Xmx2G -Xms2G | 1811.2 |
| 10.179.5.150 | 11791 | mebc-meb-service-exec.jar | 1669 / mebc-meb-service | 业务应用候选 | -Xmx3G -Xms2G | 2277.4 |
| 10.179.5.150 | 14677 | app-admin-exec.jar | 1434 / app-admin | 业务应用候选 | -Xmx2G -Xms1G | 1438.8 |
| 10.179.5.150 | 17513 | mebc-user-service-exec.jar | 1514 / mebc-user-service | 业务应用候选 | -Xmx2G -Xms2G | 1416.6 |
| 10.179.5.150 | 17517 | equity-manage-exec.jar | 1468 / equity-manage | 业务应用候选 | -Xmx3G -Xms2G | 3031 |
| 10.179.5.150 | 17805 | alipaymini-processor-exec.jar | 1904 / alipaymini-processor | 业务应用候选 | -Xmx2G -Xms1G | 2741 |
| 10.179.5.150 | 17915 | uc-facade-exec.jar | 1431 / uc-facade | 业务应用候选 | -Xmx4G -Xms4G | 5186.8 |
| 10.179.5.150 | 18009 | uc-online-exec.jar | 1432 / uc-online | 业务应用候选 | -Xmx1G -Xms1G | 1210.2 |
| 10.179.5.150 | 18169 | uc-oapi-exec.jar | 1430 / uc-oapi | 业务应用候选 | -Xmx1G -Xms1G | 1274.3 |
| 10.179.5.150 | 19091 | fcrm-exec.jar | 1453 / fcrm | 业务应用候选 | -Xmx3G -Xms2G | 3334.5 |
| 10.179.5.150 | 19462 | point-web-exec.jar | 1471 / point-web | 业务应用候选 | -Xmx2G -Xms2G | 1965.7 |
| 10.179.5.150 | 19911 | uc-admin-exec.jar | 1429 / uc-admin | 业务应用候选 | -Xmx1G -Xms1G | 1257 |
| 10.179.5.150 | 19932 | ups-api-exec.jar | 1475 / ups-api | 业务应用候选 | -Xmx2G -Xms2G | 2313.1 |
| 10.179.5.150 | 20045 | mebc-order-service-exec.jar | 1404 / mebc-order-service | 业务应用候选 | -Xmx2G -Xms2G | 2055.3 |
| 10.179.5.150 | 2066 | product-activity-service-exec.jar | 1672 / product-activity-service | 业务应用候选 | -Xmx2G -Xms1G | 2595.4 |
| 10.179.5.150 | 21938 | member-center-sync-exec.jar | 1405 / member-center-sync | 业务应用候选 | -Xmx2G -Xms2G | 2697.7 |
| 10.179.5.150 | 23498 | coupon-manage-exec.jar | 1465 / coupon-manage | 业务应用候选 | -Xmx3G -Xms2G | 3516.2 |
| 10.179.5.150 | 23786 | mebc-growth-service-exec.jar | 1406 / mebc-growth-service | 业务应用候选 | -Xmx2G -Xms2G | 2816.8 |
| 10.179.5.150 | 2398 | member-center-offline-exec.jar | 1407 / member-center-offline | 业务应用候选 | -Xmx1G -Xms1G | 1636.6 |
| 10.179.5.150 | 24075 | /opt/apache-tomcat_foliday_meb | 1460 / foliday_meb（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 2214 |
| 10.179.5.150 | 25101 | mebc-privilege-service-exec.jar | 1408 / mebc-privilege-service | 业务应用候选 | -Xmx1G -Xms1G | 1738.3 |
| 10.179.5.150 | 25327 | marketing-center-activity-front-service-exec.jar | 1382 / marketing-center-activity-front-service | 业务应用候选 | -Xmx4G -Xms2G | 2321.8 |
| 10.179.5.150 | 25960 | cls-api-exec.jar | 1442 / cls-api | 业务应用候选 | -Xmx4G -Xms4G | 2308.5 |
| 10.179.5.150 | 26705 | mebc-point-service-exec.jar | 1839 / mebc-point-service | 业务应用候选 | -Xmx3G -Xms2G | 3836.2 |
| 10.179.5.150 | 28235 | point-manage-exec.jar | 1472 / point-manage | 业务应用候选 | -Xmx2G -Xms2G | 1369.8 |
| 10.179.5.150 | 28363 | crm-markting-notify-api-exec.jar | 1537 / crm-markting-notify-api | 业务应用候选 | -Xmx4G -Xms4G | 2173.9 |
| 10.179.5.150 | 28462 | marketing-center-offline-exec.jar | 1380 / marketing-center-offline | 业务应用候选 | -Xmx4G -Xms2G | 3526.5 |
| 10.179.5.150 | 29021 | coupon-web-exec.jar | 1464 / coupon-web | 业务应用候选 | -Xmx3G -Xms2G | 3617.4 |
| 10.179.5.150 | 29685 | equity-web-exec.jar | 1469 / equity-web | 业务应用候选 | -Xmx2G -Xms2G | 2631.7 |
| 10.179.5.150 | 29687 | cooperate-union-service-exec.jar | 1598 / cooperate-union-service | 业务应用候选 | -Xmx1G -Xms1G | 1273.8 |
| 10.179.5.150 | 29893 | alipaymini-web-exec.jar | 1565 / alipaymini-web | 业务应用候选 | -Xmx3G -Xms2G | 3029.9 |
| 10.179.5.150 | 30977 | service-api-exec.jar | 1467 / service-api | 业务应用候选 | -Xmx3G -Xms2G | 1785.1 |
| 10.179.5.150 | 31038 | fcrm-consumer-exec.jar | 1470 / fcrm-consumer | 业务应用候选 | -Xmx3G -Xms2G | 2114.9 |
| 10.179.5.150 | 31274 | /opt/apache-tomcat_smart-sso-server | 1458 / smart-sso-server（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1081.1 |
| 10.179.5.150 | 31541 | marketing-equity-front-service-exec.jar | 1804 / marketing-equity-front-service | 业务应用候选 | -Xmx2G -Xms1G | 2692.2 |
| 10.179.5.150 | 4275 | cooperate-offline-exec.jar | 1610 / cooperate-offline | 业务应用候选 | -Xmx3G -Xms2G | 2984.7 |
| 10.179.5.150 | 5334 | app-api-exec.jar | 1433 / app-api | 业务应用候选 | -Xmx4G -Xms4G | 2745.1 |
| 10.179.5.150 | 5646 | active-marketing-service-exec.jar | 1650 / active-marketing-service | 业务应用候选 | -Xmx2G -Xms1G | 1655.3 |
| 10.179.5.150 | 6197 | coupon-consumer-exec.jar | 1466 / coupon-consumer | 业务应用候选 | -Xmx3G -Xms2G | 2051.3 |
| 10.179.5.150 | 8214 | live-center-offline-exec.jar | 1860 / live-center-offline | 业务应用候选 | -Xmx2G -Xms1G | 2211 |
| 10.179.5.151 | 11689 | /home/app/tomcat_feedback_9080 | 待核实 / 待核实 | 待确认进程 | -Xms128m -Xmx1024m | 711.5 |
| 10.179.5.151 | 13749 | app-api-exec.jar | 1433 / app-api | 业务应用候选 | -Xmx4G -Xms4G | 2653.9 |
| 10.179.5.151 | 22312 | /opt/apache-tomcat_fostay-sys-order | 1398 / fostay-sys-order（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 3924.8 |
| 10.179.5.151 | 29834 | fcrm-job-exec.jar | 1882 / fcrm-job | 业务应用候选 | -Xmx2G -Xms2G | 1692.6 |
| 10.179.5.151 | 30086 | /home/app/tomcat_feedback_9090 | 待核实 / 待核实 | 待确认进程 | -Xms128m -Xmx1024m | 653 |
| 10.179.5.151 | 30962 | cls-api-exec.jar | 1442 / cls-api | 业务应用候选 | -Xmx4G -Xms4G | 2326.1 |
| 10.179.5.151 | 32487 | /opt/apache-tomcat_foliday-mms | 1851 / foliday-mms（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1358.1 |
| 10.179.5.151 | 7032 | merchant-api-exec.jar | 1489 / merchant-api | 业务应用候选 | -Xmx4G -Xms4G | 2283.7 |
| 10.179.5.151 | 7822 | foyue-openapi-exec.jar | 1485 / foyue-openapi | 业务应用候选 | -Xmx2G -Xms1G | 1033.3 |
| 10.179.5.151 | 9662 | foyue-manage-exec.jar | 1488 / foyue-manage | 业务应用候选 | -Xmx2G -Xms1G | 947.3 |
| 10.179.5.152 | 23098 | uc-facade-exec.jar | 1431 / uc-facade | 业务应用候选 | -Xmx4G -Xms4G | 5132.6 |
| 10.179.5.152 | 23137 | uc-online-exec.jar | 1432 / uc-online | 业务应用候选 | -Xmx1G -Xms1G | 1132.3 |
| 10.179.5.152 | 29644 | foyue-manage-exec.jar | 1488 / foyue-manage | 业务应用候选 | -Xmx2G -Xms1G | 935.6 |
| 10.179.5.152 | 29895 | foyue-openapi-exec.jar | 1485 / foyue-openapi | 业务应用候选 | -Xmx2G -Xms1G | 1013.8 |
| 10.179.5.152 | 31885 | /opt/apache-tomcat_foliday-mms | 1851 / foliday-mms（推断） | 业务应用候选 | -Xmx3072M -Xms2048M -Xss256k | 1227.9 |
| 10.179.5.152 | 5508 | merchant-api-exec.jar | 1489 / merchant-api | 业务应用候选 | -Xmx4G -Xms4G | 2206.6 |
| 10.179.5.152 | 8299 | fcrm-job-exec.jar | 1882 / fcrm-job | 业务应用候选 | -Xmx2G -Xms2G | 1684.4 |
| 10.179.5.153 | 15880 | photo-standard-job-exec.jar | 1504 / photo-standard-job | 业务应用候选 | -Xmx3G -Xms2G | 1322.7 |
| 10.179.5.154 | 17518 | photo-standard-job-exec.jar | 1504 / photo-standard-job | 业务应用候选 | -Xmx3G -Xms2G | 1339.4 |
| 10.179.5.155 | 223141 | fosun-legacy-adapter-service-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 1882.5 |
| 10.179.5.155 | 228030 | phoenix-product-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 1982.8 |
| 10.179.5.155 | 229275 | phoenix-preprocess-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 3113.7 |
| 10.179.5.155 | 232106 | phoenix-order-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 2137.2 |
| 10.179.5.155 | 235914 | fcrm-order-center-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 2553.7 |
| 10.179.5.156 | 120305 | phoenix-product-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 1987.4 |
| 10.179.5.156 | 121625 | phoenix-preprocess-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 2061.7 |
| 10.179.5.156 | 124599 | fosun-legacy-adapter-service-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 1903.1 |
| 10.179.5.156 | 125182 | phoenix-order-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 2199.5 |
| 10.179.5.156 | 126944 | fcrm-order-center-exec.jar | 待核实 / 待核实 | 业务应用候选 | -Xms2g -Xmx2g -Xss512k | 2504.9 |
| 10.179.5.158 | 107317 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms4m -Xmx64m | 110.5 |
| 10.179.5.158 | 107388 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xss1m -Xms16g -Xmx16g -XX:MaxDirectMemorySize=8589934592 | 19696 |
| 10.179.5.158 | 41605 | xxl-job-admin-3.4.2.jar | 待核实 / 待核实 | 待确认进程 | -Xms512m -Xmx512m | 747.6 |
| 10.179.5.34 | 10174 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xms1g -Xmx1g -Xss1m -XX:MaxDirectMemorySize=536870912 | 1403.2 |
| 10.179.5.34 | 9485 | 待核实 | 待核实 / 待核实 | 待确认进程 | -Xms1g -Xmx1g | 1417.7 |
| 10.179.5.40 | 25505 | Elasticsearch | 待核实 / Elasticsearch（启动命令识别的组件） | 待确认进程 | -Xss1m -Xms16059m -Xmx16059m -XX:MaxDirectMemorySize=8422162432 | 19038.7 |

共 439 条 JVM 进程候选记录，含待确认记录。该清单供评估，不是自动减配建议；进程覆盖和原始行号详见来源工作簿。
