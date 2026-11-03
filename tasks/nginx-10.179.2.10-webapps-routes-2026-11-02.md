# 10.179.2.10 Nginx 本地静态路径核对

- 采集时间：2026-11-02 18:03:40（北京时间）；以下为采集时配置，并非实时状态。
- 启用数据版本：`20261102-203114-680124`；Nginx 配置归档版本：`20261102-180003-161455`。
- 原始归档的非注释指令中，73 条 alias、6 条 root 指向 /var/www/webapps；下表为归一化的 90 条路由上下文，同一配置可由多个 server 引用。
- 原始配置可能包含其他条件和重写；表格仅摘录已归一化的路由，不能证明当前文件仍存在或流量正在使用。

| 域名 | 监听 | Location | 本地目标 | 配置文件 |
| --- | --- | --- | --- | --- |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | / | /var/www/webapps/fsop-admin-ui/ | /etc/nginx/conf.d/uatadmin.folidaymall.com.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ~ /druid/ | /var/www/webapps/fsop-admin-ui | /etc/nginx/conf.d/uatadmin.folidaymall.com.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | /spark-company | /var/www/webapps/spark-company | /etc/nginx/config/admin.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | /spark-platform | /var/www/webapps/spark-platform | /etc/nginx/config/admin.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /erp | /var/www/webapps/erp | /etc/nginx/config/admin.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | /financesystem | /var/www/webapps/financesystem | /etc/nginx/config/admin.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /businessadmin | /var/www/webapps/businessadmin | /etc/nginx/config/admin.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /memberadmin | /var/www/webapps/memberadmin | /etc/nginx/config/admin.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /hoteladmin | /var/www/webapps/hoteladmin | /etc/nginx/config/admin.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /giftcardadmin | /var/www/webapps/giftcardadmin | /etc/nginx/config/admin.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | /envInfo | /var/www/webapps/fsop-admin-ui/envInfo | /etc/nginx/config/env_uat.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /desktop | /var/www/webapps/merchant-desktop | /etc/nginx/config/merchant.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /tools | /var/www/webapps/merchant-platform-tools | /etc/nginx/config/merchant.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /m | /var/www/webapps/merchant-h5-m | /etc/nginx/config/merchant.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /hsm | /var/www/webapps/hsm | /etc/nginx/config/mbp.conf |
| adminuat.fosunholiday.com, uatadmin.folidaymall.com | 80 | ^~ /pms | /var/www/webapps/hsm | /etc/nginx/config/mbp.conf |
| uatapi.folidaymall.com, uatopen.folidaymall.com | 80 | ^~ /desktop | /var/www/webapps/merchant-desktop | /etc/nginx/config/merchant.conf |
| uatapi.folidaymall.com, uatopen.folidaymall.com | 80 | ^~ /tools | /var/www/webapps/merchant-platform-tools | /etc/nginx/config/merchant.conf |
| uatapi.folidaymall.com, uatopen.folidaymall.com | 80 | ^~ /m | /var/www/webapps/merchant-h5-m | /etc/nginx/config/merchant.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /member-rights-new | /var/www/webapps/member-rights-new | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /seller-pos | /var/www/webapps/h5-seller-pos | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /lucky | /var/www/webapps/lucky | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | / | /var/www/webapps/foliday-m/ | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | /tc-svip | /var/www/webapps/tc-svip | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /bank-session | /var/www/webapps/bank-session-portal | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /unionpay | /var/www/webapps/unionpay | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /signin | /var/www/webapps/signin | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /login | /var/www/webapps/h5-login | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /content | /var/www/webapps/TC-H5-contentDetail | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /tc-personalcenter | /var/www/webapps/tc-personalCenter | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /member-rights | /var/www/webapps/member-rights | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /tc-laomiao | /var/www/webapps/tc-laomiao | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /download | /var/www/webapps/download | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /cooksclub | /var/www/webapps/cooksclub | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /static | /var/www/webapps/static | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /fcrs-console | /var/www/webapps/fcrs-console | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /point-treasure | /var/www/webapps/point-treasure | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /fmsg-console | /var/www/webapps/fmsg-console | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /single | /var/www/webapps/singlepage | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /fyh | /var/www/webapps/fyh | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /tc-fostay | /var/www/webapps/tc-fostay | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /couponuse | /var/www/webapps/couponuse | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /activity | /var/www/webapps/activity-m-template | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /posweb-plus | /var/www/webapps/posweb-plus | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /payportal | /var/www/webapps/payportal | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /udesk-erp | /var/www/webapps/udesk-erp | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /tc-erp | /var/www/webapps/tc-erp | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /jwx-erp | /var/www/webapps/jwx-erp | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /tc-serverguide | /var/www/webapps/tc-serverguide | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /hotel-fotel-online | /var/www/webapps/hotel-bank-session | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /tc-steward | /var/www/webapps/tc-steward | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /user-zone | /var/www/webapps/External-Cooperation-Project | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /hotel-fotel | /var/www/webapps/hotel-fotel | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uath5.folidaymall.com, uathotels.folidaymall.com | 80 | ^~ /hotel-joy | /var/www/webapps/hotel-joy | /etc/nginx/conf.d/uath5.folidaymall.com.conf |
| uathm.folidaymall.com | 80 | / | /var/www/webapps/hm-m-main/ | /etc/nginx/config/uathm.conf |
| uatm-trip.folidaymall.com, testm-trip.folidaymall.com | 80 | ^~ / | /var/www/webapps/app-gateway/ | /etc/nginx/conf.d/uatm-trip.folidaymall.com.conf |
| uatm-trip.folidaymall.com, testm-trip.folidaymall.com | 80 | ^~ /resource/car/ | /var/www/webapps/app-car/ | /etc/nginx/conf.d/uatm-trip.folidaymall.com.conf |
| uatm-trip.folidaymall.com, testm-trip.folidaymall.com | 80 | ^~ /resource/common/ | /var/www/webapps/app-common/ | /etc/nginx/conf.d/uatm-trip.folidaymall.com.conf |
| uatm-trip.folidaymall.com, testm-trip.folidaymall.com | 80 | ^~ /resource/expense/ | /var/www/webapps/app-expense/ | /etc/nginx/conf.d/uatm-trip.folidaymall.com.conf |
| uatm-trip.folidaymall.com, testm-trip.folidaymall.com | 80 | ^~ /resource/flight/ | /var/www/webapps/app-flight/ | /etc/nginx/conf.d/uatm-trip.folidaymall.com.conf |
| uatm-trip.folidaymall.com, testm-trip.folidaymall.com | 80 | ^~ /resource/hotel/ | /var/www/webapps/app-hotel/ | /etc/nginx/conf.d/uatm-trip.folidaymall.com.conf |
| uatm-trip.folidaymall.com, testm-trip.folidaymall.com | 80 | ^~ /resource/train/ | /var/www/webapps/app-train/ | /etc/nginx/conf.d/uatm-trip.folidaymall.com.conf |
| uatmbp.folidaymall.com | 80 | ^~ /hsm | /var/www/webapps/hsm | /etc/nginx/config/mbp.conf |
| uatmbp.folidaymall.com | 80 | ^~ /pms | /var/www/webapps/hsm | /etc/nginx/config/mbp.conf |
| uatmerchant.folidaymall.com | 80 | ^~ /desktop | /var/www/webapps/merchant-desktop | /etc/nginx/config/merchant.conf |
| uatmerchant.folidaymall.com | 80 | ^~ /tools | /var/www/webapps/merchant-platform-tools | /etc/nginx/config/merchant.conf |
| uatmerchant.folidaymall.com | 80 | ^~ /m | /var/www/webapps/merchant-h5-m | /etc/nginx/config/merchant.conf |
| uatmice.folidaymall.com | 80 | ~ /druid/ | /var/www/webapps/groupActive | /etc/nginx/conf.d/uatmice.folidaymall.com.conf |
| uatmice.folidaymall.com | 80 | / | /var/www/webapps/groupActive/ | /etc/nginx/conf.d/uatmice.folidaymall.com.conf |
| uatpay.folidaymall.com | 80 | /payportal | /var/www/webapps/payportal | /etc/nginx/conf.d/uatpay.folidaymall.com.conf |
| uatpos.folidaymall.com | 80 | ^~ /seller-pos | /var/www/webapps/h5-seller-pos | /etc/nginx/config/uatpos.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /main | /var/www/webapps/tcs_ui_main | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-distribution | /var/www/webapps/micro-app-distribution | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-psis | /var/www/webapps/micro-app-psis | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-member | /var/www/webapps/micro-app-member | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-seller | /var/www/webapps/micro-app-seller | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-retail | /var/www/webapps/micro-app-retail | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-vacation | /var/www/webapps/micro-app-vacation | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-coupon | /var/www/webapps/micro-app-coupon | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-sale | /var/www/webapps/micro-app-sale | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-basicinfo | /var/www/webapps/micro-app-basicinfo | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-platform | /var/www/webapps/micro-app-platform | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-hotel | /var/www/webapps/micro-app-hotel | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-uksystem | /var/www/webapps/tcs_ui_system | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-news | /var/www/webapps/micro-app-news | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-order-center | /var/www/webapps/micro-app-order-center | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-hsmsettlement | /var/www/webapps/business-application | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-supplier | /var/www/webapps/micro-app-supplier | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-financesystem | /var/www/webapps/micro-app-financesystem | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
| uattcs.folidaymall.com, ~^uattcss\d+\.folidaymall\.com$, ~^uattcsc\d+\.folidaymall\.com$ | 80 | /micro-app-operate | /var/www/webapps/micro-app-market | /etc/nginx/conf.d/uattcs.folidaymall.com.conf |
