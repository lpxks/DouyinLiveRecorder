"""streamget 映射测试:spider 平台函数 → streamget 类的映射表完整性。

映射表与 src/spider.py 中的包装实现保持一致;此处独立维护一份,
用轻量 import(streamget)校验类名/字段/登录构造签名,不 import spider。
"""

import dataclasses
import inspect
import unittest

import streamget

# spider 函数名 → streamget 类名(与 src/spider.py 包装实现一致)
MAPPING = {
    "get_douyin_web_stream_data": "DouyinLiveStream",
    "get_douyin_app_stream_data": "DouyinLiveStream",
    "get_tiktok_stream_data": "TikTokLiveStream",
    "get_kuaishou_stream_data": "KwaiLiveStream",
    "get_huya_stream_data": "HuyaLiveStream",
    "get_huya_app_stream_url": "HuyaLiveStream",
    "get_douyu_info_data": "DouyuLiveStream",
    "get_yy_stream_data": "YYLiveStream",
    "get_bilibili_room_info": "BilibiliLiveStream",
    "get_bilibili_stream_data": "BilibiliLiveStream",
    "get_xhs_stream_url": "RedNoteLiveStream",
    "get_bigo_stream_url": "BigoLiveStream",
    "get_blued_stream_url": "BluedLiveStream",
    "get_netease_stream_data": "NeteaseLiveStream",
    "get_qiandurebo_stream_data": "QiandureboLiveStream",
    "get_pandatv_stream_data": "PandaLiveStream",
    "get_maoerfm_stream_url": "MaoerLiveStream",
    "get_winktv_stream_data": "WinkTVLiveStream",
    "get_looklive_stream_url": "LookLiveStream",
    "get_baidu_stream_data": "BaiduLiveStream",
    "get_weibo_stream_data": "WeiboLiveStream",
    "get_kugou_stream_url": "KugouLiveStream",
    "get_twitchtv_stream_data": "TwitchLiveStream",
    "get_liveme_stream_url": "LiveMeLiveStream",
    "get_huajiao_stream_url": "HuajiaoLiveStream",
    "get_showroom_stream_data": "ShowRoomLiveStream",
    "get_acfun_stream_data": "AcfunLiveStream",
    "get_changliao_stream_url": "ChangliaoLiveStream",
    "get_yingke_stream_url": "InkeLiveStream",
    "get_yinbo_stream_url": "YinboLiveStream",
    "get_zhihu_stream_url": "ZhihuLiveStream",
    "get_chzzk_stream_data": "ChzzkLiveStream",
    "get_haixiu_stream_url": "HaixiuLiveStream",
    "get_vvxqiu_stream_url": "VVXQLiveStream",
    "get_17live_stream_url": "YiqiLiveStream",
    "get_langlive_stream_url": "LangLiveStream",
    "get_pplive_stream_url": "PiaopaioLiveStream",
    "get_6room_stream_url": "SixRoomLiveStream",
    "get_shopee_stream_url": "ShopeeLiveStream",
    "get_youtube_stream_url": "YoutubeLiveStream",
    "get_taobao_stream_url": "TaobaoLiveStream",
    "get_jd_stream_url": "JDLiveStream",
    "get_faceit_stream_data": "FaceitLiveStream",
    "get_migu_stream_url": "MiguLiveStream",
    "get_lianjie_stream_url": "LianJieLiveStream",
    "get_laixiu_stream_url": "LaixiuLiveStream",
    "get_picarto_stream_url": "PicartoLiveStream",
    "get_sooplive_stream_data": "SoopLiveStream",
    "get_flextv_stream_data": "FlexTVLiveStream",
    "get_popkontv_stream_url": "PopkonTVLiveStream",
    "get_twitcasting_stream_url": "TwitCastingLiveStream",
}

STREAMDATA_FIELDS = {
    "platform", "anchor_name", "is_live", "title", "quality",
    "m3u8_url", "flv_url", "record_url", "new_cookies", "new_token",
    "extra", "live_url",
}

LOGIN_PLATFORMS = {
    "SoopLiveStream": ("username", "password"),
    "FlexTVLiveStream": ("username", "password"),
    "PopkonTVLiveStream": ("username", "password", "access_token", "partner_code"),
    "TwitCastingLiveStream": ("username", "password", "account_type"),
}


class StreamgetMappingTest(unittest.TestCase):
    def test_all_mapped_classes_exist(self):
        missing = [cls for cls in MAPPING.values() if not hasattr(streamget, cls)]
        self.assertEqual(missing, [], f"streamget 中缺少类: {missing}")

    def test_mapping_covers_demo_platforms(self):
        """demo.py 引用的平台函数(除流星)都应已在映射表中。"""
        import re
        from pathlib import Path
        demo_src = (Path(__file__).resolve().parents[1] / "demo.py").read_text(encoding="utf-8")
        refs = set(re.findall(r"spider\.(get_[a-zA-Z0-9_]+)", demo_src))
        unmapped = refs - set(MAPPING) - {"get_liuxing_stream_url"}
        self.assertFalse(unmapped, f"demo 平台未在映射表中: {unmapped}")

    def test_streamdata_fields(self):
        fields = {f.name for f in dataclasses.fields(streamget.StreamData)}
        self.assertGreaterEqual(fields, STREAMDATA_FIELDS,
                                f"StreamData 缺少字段: {STREAMDATA_FIELDS - fields}")

    def test_login_platform_constructors(self):
        for cls_name, expected in LOGIN_PLATFORMS.items():
            cls = getattr(streamget, cls_name)
            params = [p for p in inspect.signature(cls.__init__).parameters if p != "self"]
            for p in expected:
                self.assertIn(p, params, f"{cls_name} 构造缺少参数 {p}")


if __name__ == "__main__":
    unittest.main()
