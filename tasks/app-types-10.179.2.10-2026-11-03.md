# 10.179.2.10 关联应用类型核查

- 范围：DevOps 快照中所有环境关联到该 IP 的 82 个去重应用；本批实际均为 SIMULATION。快照采集时间：2026-11-02 20:36:54（北京时间），数据版本 `20261102-203114-680124`。
- 类型列保留 DevOps 的原始 `appType`。仓库组和 H5 标签仅是仓库路径命名线索；H5 与 Node 并不互斥，不能仅凭 `node` 或仓库名称断定运行时、页面形态或框架。Node 类可能是构建工具，也可能是独立服务。
- 静态目录列表示 2026-11-02 18:03:40 的 Nginx 配置目标路径第一段与应用名精确同名，属于名称关联，不证明目录存在、流量生效或部署归属；未发现同名目录也不证明没有静态入口。见 [Nginx 路由证据](nginx-10.179.2.10-webapps-routes-2026-11-02.md)。
- 进程列依据同一时刻的完整主机进程快照中应用名与 `-Dappid` / `-Danchor.domainName` 精确匹配；无匹配不等于应用未运行。Nginx 是共享服务，其 PID 与 DevOps 应用的独占关系未核实。
- 源节点状态：13 项为 2026-11-03 00:02 DevOps 仿真页面复核，其余沿用历史快照且未逐项实时复核；源记录消失不等于文件已删除。

| 应用 ID | 应用名 | DevOps 类型 | 分类及线索 | Nginx 同名目录 | 源节点记录 | 进程证据 | DevOps 仓库路径 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1886 | tc-folidayclub | node_backend | Node 后端（登记） | 未发现同名目录 | 记录未显示 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/tc-folidayclub.git |
| 1875 | thomascook | node_backend | Node 后端（登记） | 未发现同名目录 | 记录未显示 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/thomasCook.git |
| 1868 | udesk-erp | node | Node 类；frontend-b 仓库组 | 同名目录：`udesk-erp` | 记录未显示 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcg-spark/udesk-erp.git |
| 1859 | jwx-erp | node | Node 类；H5 仓库组 | 同名目录：`jwx-erp` | 记录未显示 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/jwx-erp.git |
| 1857 | Nginx | nginx_yum | Nginx 中间件（登记） | 未发现同名目录 | 记录存在 | 共享 Nginx：主 PID 1031；未确认专属 | — |
| 1856 | hotel-joy | node | Node 类；其他仓库组 | 同名目录：`hotel-joy` | 记录未显示 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/backend-order/hotels/hotel-joy.git |
| 1847 | spark-company | node | Node 类；frontend-b 仓库组 | 同名目录：`spark-company` | 记录未显示 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcg-spark/spark-company.git |
| 1843 | micro-app-distribution | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-distribution` | 记录未显示 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-distribution.git |
| 1841 | spark-platform | node | Node 类；frontend-b 仓库组 | 同名目录：`spark-platform` | 记录未显示 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcg-spark/spark-platform.git |
| 1840 | f-photo-h5 | node | Node 类；frontend-c 仓库组 | 未发现同名目录 | 记录未显示 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-atlantis/frontend-c/fphoto-website.git |
| 1838 | photo-opeadmin | node | Node 类；frontend-c 仓库组 | 未发现同名目录 | 记录存在 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-atlantis/frontend-c/fphoto-website.git |
| 1836 | micro-app-market | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-market` | 记录存在 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-market.git |
| 1834 | micro-app-psis | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-psis` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-psis.git |
| 1833 | micro-app-seller | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-seller` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-seller.git |
| 1832 | micro-app-retail | node | Node 类；其他仓库组 | 同名目录：`micro-app-retail` | 未实时复核 | 无精确匹配 | tcs_ui/micro-app-retail.git |
| 1823 | micro-app-member | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-member` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-member.git |
| 1822 | micro-app-vacation | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-vacation` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-vacation.git |
| 1821 | micro-app-coupon | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-coupon` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-coupon.git |
| 1820 | micro-app-financesystem | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-financesystem` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-financesystem.git |
| 1819 | micro-app-sale | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-sale` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-sale.git |
| 1811 | member-rights-new | node | Node 类；H5 仓库组 | 同名目录：`member-rights-new` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/member-rights-new.git |
| 1808 | micro-app-platform | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-platform` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/intelligent-platform.git |
| 1776 | merchant-h5-m | node | Node 类；frontend-b 仓库组 | 同名目录：`merchant-h5-m` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/merchant--web-m.git |
| 1768 | groupActive | node | Node 类；H5 仓库组 | 同名目录：`groupActive` | 未实时复核 | 无精确匹配 | fosunholiday_H5_active/groupActive.git |
| 1767 | tem-pc-admin | node | Node 类；H5 仓库组 | 未发现同名目录 | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Ftrip/fosun-h5/tem-pc-admin.git |
| 1766 | micro-app-hotel | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-hotel` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-hotel.git |
| 1764 | hm-m-main | node | Node 类；H5 仓库组 | 同名目录：`hm-m-main` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/foliday.git |
| 1761 | business-application | node | Node 类；frontend-b 仓库组 | 同名目录：`business-application` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/business-application.git |
| 1759 | micro-app-news | node | Node 类；frontend-b 仓库组 | 同名目录：`micro-app-news` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/micro-app-news.git |
| 1757 | tcs_ui_system | node | Node 类；frontend-b 仓库组 | 同名目录：`tcs_ui_system` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/system.git |
| 1756 | tcs_ui_main | node | Node 类；frontend-b 仓库组 | 同名目录：`tcs_ui_main` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcs-ui/main.git |
| 1708 | app-expense | node | Node 类；H5 仓库组 | 同名目录：`app-expense` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Ftrip/fosun-h5/app-expense.git |
| 1707 | app-train | node | Node 类；H5 仓库组 | 同名目录：`app-train` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Ftrip/fosun-h5/app-train.git |
| 1706 | app-hotel | node | Node 类；H5 仓库组 | 同名目录：`app-hotel` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Ftrip/fosun-h5/app-hotel.git |
| 1705 | app-car | node | Node 类；H5 仓库组 | 同名目录：`app-car` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Ftrip/fosun-h5/app-car.git |
| 1704 | tem-pc-common | node | Node 类；H5 仓库组 | 未发现同名目录 | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Ftrip/fosun-h5/tem-pc-common.git |
| 1697 | app-flight | node | Node 类；H5 仓库组 | 同名目录：`app-flight` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Ftrip/fosun-h5/app-flight.git |
| 1693 | app-common | node | Node 类；H5 仓库组 | 同名目录：`app-common` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Ftrip/fosun-h5/app-common.git |
| 1690 | app-gateway | node | Node 类；H5 仓库组 | 同名目录：`app-gateway` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Ftrip/fosun-h5/app-gateway.git |
| 1674 | hotel-bank-session | node | Node 类；frontend-b 仓库组 | 同名目录：`hotel-bank-session` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/hotel-bank-session.git |
| 1670 | External-Cooperation-Project | node | Node 类；H5 仓库组 | 同名目录：`External-Cooperation-Project` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/External-Cooperation-Project.git |
| 1668 | erp | node | Node 类；frontend-b 仓库组 | 同名目录：`erp` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/tcg-spark/spark-admin.git |
| 1664 | h5-seller-pos | node | Node 类；H5 仓库组 | 同名目录：`h5-seller-pos` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/h5-seller-pos.git |
| 1655 | fyh-member-rights(重复废弃) | node | Node 类；frontend-c 仓库组 | 未发现同名目录 | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-c/fyh-member-rights.git |
| 1653 | pms | node | Node 类；其他仓库组 | 未发现同名目录 | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/foliday-admin/pms.git |
| 1635 | tc-steward | node | Node 类；H5 仓库组 | 同名目录：`tc-steward` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/tc-steward.git |
| 1631 | unionpay | node | Node 类；其他仓库组 | 同名目录：`unionpay` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/foliday/unionpay-ui.git |
| 1611 | TC-H5-contentDetail | node | Node 类；H5 仓库组 | 同名目录：`TC-H5-contentDetail` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/TC-H5-contentDetail.git |
| 1606 | merchant-platform-tools | node | Node 类；frontend-b 仓库组 | 同名目录：`merchant-platform-tools` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/merchant-platform-tools.git |
| 1604 | cooksclub | node | Node 类；H5 仓库组 | 同名目录：`cooksclub` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/cooksclub.git |
| 1603 | fmsg-console | node | Node 类；其他仓库组 | 同名目录：`fmsg-console` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Fotel/fmsg-console.git |
| 1601 | financesystem | node | Node 类；frontend-b 仓库组 | 同名目录：`financesystem` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/finance.git |
| 1600 | tc-serverguide | node | Node 类；H5 仓库组 | 同名目录：`tc-serverguide` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/tc-serverguide.git |
| 1599 | lucky | node | Node 类；H5 仓库组 | 同名目录：`lucky` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/activity.git |
| 1597 | fcrs-console | node | Node 类；其他仓库组 | 同名目录：`fcrs-console` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/Fotel/fcrs-console.git |
| 1595 | tc-personalCenter | node | Node 类；H5 仓库组 | 同名目录：`tc-personalCenter` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/tc-personalCenter.git |
| 1589 | tc-svip | node | Node 类；H5 仓库组 | 同名目录：`tc-svip` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/tc-superVipCard.git |
| 1582 | singlepage | node | Node 类；H5 仓库组 | 同名目录：`singlepage` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/TC-H5-SinglePage.git |
| 1581 | signin | node | Node 类；H5 仓库组 | 同名目录：`signin` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/signIn.git |
| 1580 | tc-laomiao | node | Node 类；H5 仓库组 | 同名目录：`tc-laomiao` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/tc-laomiao.git |
| 1578 | download | node | Node 类；H5 仓库组 | 同名目录：`download` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/download.git |
| 1575 | member-rights | node | Node 类；frontend-c 仓库组 | 同名目录：`member-rights` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-c/fyh-member-rights.git |
| 1568 | fyh | node | Node 类；其他仓库组 | 同名目录：`fyh` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fyh/newFYH.git |
| 1564 | couponget | node | Node 类；H5 仓库组 | 未发现同名目录 | 记录存在 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/coupon-get.git |
| 1562 | static | node | Node 类；H5 仓库组 | 同名目录：`static` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/static.git |
| 1561 | point-treasure | node | Node 类；H5 仓库组 | 同名目录：`point-treasure` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/point-treasure.git |
| 1556 | couponuse | node | Node 类；H5 仓库组 | 同名目录：`couponuse` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/coupon-use.git |
| 1554 | hsm | node | Node 类；frontend-b 仓库组 | 同名目录：`hsm` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/hsm.git |
| 1549 | hoteladmin | node | Node 类；其他仓库组 | 同名目录：`hoteladmin` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/backend-cms/hotels-cms.git |
| 1548 | giftcardadmin | node | Node 类；H5 仓库组 | 同名目录：`giftcardadmin` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/giftCard-admin.git |
| 1547 | businessadmin | node | Node 类；H5 仓库组 | 同名目录：`businessadmin` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/business-admin.git |
| 1546 | memberadmin | node | Node 类；H5 仓库组 | 同名目录：`memberadmin` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/memberadmin.git |
| 1545 | posweb-plus | node | Node 类；H5 仓库组 | 同名目录：`posweb-plus` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/posweb-plus.git |
| 1544 | merchant-desktop | node | Node 类；frontend-b 仓库组 | 同名目录：`merchant-desktop` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/merchant--web-pc.git |
| 1541 | tc-erp | node | Node 类；H5 仓库组 | 同名目录：`tc-erp` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-h5/tc-erp.git |
| 1539 | activity-m-template | node | Node 类；frontend-b 仓库组 | 同名目录：`activity-m-template` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/activity-m-template.git |
| 1527 | payportal | node | Node 类；H5 仓库组 | 同名目录：`payportal` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fh5/tc-pay-portal.git |
| 1526 | foliday-m | node | Node 类；frontend-b 仓库组 | 同名目录：`foliday-m` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/foliday-m.git |
| 1525 | h5-login | node | Node 类；frontend-c 仓库组 | 同名目录：`h5-login` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-atlantis/frontend-c/atlantisLogin.git |
| 1484 | fsop-admin-ui | node | Node 类；frontend-b 仓库组 | 同名目录：`fsop-admin-ui` | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fosun-fyh/frontend-b/foliday-sop-ui.git |
| 1440 | fomall-gateway | app | 应用服务（登记 app） | 未发现同名目录 | 未实时复核 | PID 21194，Java | 61a9cf2465322dff5b9e01f2/fcrm/point-mall-new.git |
| 1373 | base-api | app | 应用服务（登记 app） | 未发现同名目录 | 未实时复核 | 无精确匹配 | 61a9cf2465322dff5b9e01f2/fcrm/point-mall-new.git |
