from __future__ import annotations

from ..http import make_client
from ..models import Tender
from ..textutil import clean_text, parse_amount, parse_date
from .base import BaseSource, SourceError

API = (
    "https://www.cebpubservice.com/ctpsp_iiss/"
    "searchbusinesstypebeforedooraction/getStringMethod.do"
)
PORTAL = (
    "https://www.cebpubservice.com/ctpsp_iiss/"
    "searchbusinesstypebeforedooraction/getSearch.do"
)
DETAIL = (
    "https://www.cebpubservice.com/ctpsp_iiss/"
    "searchbusinesstypebeforedooraction/showDetails.do"
)

BUSINESS_TYPES = {
    "tender": "招标公告",
    "award": "中标公告",
    "candidate": "评标公示",
    "open": "开标记录",
    "qualify": "资格预审公告",
}


class CebpubSource(BaseSource):
    """中国招标投标公共服务平台（全国招标公告/公示 JSON 接口）。"""

    id = "cebpub"
    label = "中国招标投标公共服务平台"
    referer = PORTAL
    min_interval = 1.0

    def search(
        self,
        keyword: str,
        days: int = 7,
        limit: int = 50,
        business_type: str = "tender",
    ) -> list[Tender]:
        types = self._resolve_types(business_type)
        cutoff = self.cutoff(days)
        results: list[Tender] = []
        per_type_limit = max(1, (limit + len(types) - 1) // max(len(types), 1))

        for type_name in types:
            # 平台规则：每个“关键词 × 业务类型”组合只有新会话的首次查询免 VAPTCHA。
            # 因此每次查询都重置会话，并且只抓第一页。
            self._reset_session()
            page = 1
            collected = 0
            while collected < per_type_limit and page <= 1:
                payload = {
                    "searchName": keyword,
                    "searchArea": "",
                    "searchIndustry": "",
                    "centerPlat": "",
                    "businessType": type_name,
                    "searchTimeStart": "",
                    "searchTimeStop": "",
                    "timeTypeParam": "",
                    "bulletinIssnTime": "",
                    "bulletinIssnTimeStart": "",
                    "bulletinIssnTimeStop": "",
                    "pageNo": page,
                    "row": 50,
                }
                response = self.post(
                    API,
                    data=payload,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": PORTAL,
                    },
                )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise SourceError("cebpub 返回了非 JSON 内容") from exc
                if not data.get("success"):
                    message = clean_text(data.get("message") or data.get("object") or "")
                    if "验证码" in message or "v-token" in message:
                        raise SourceError(
                            "cebpub 要求 VAPTCHA 验证码（该 IP 或会话已被限制）；"
                            "请降低频率、稍后重试，或改用 Crawl4AI 浏览器模式"
                        )
                    raise SourceError(f"cebpub 查询失败: {message or '未知错误'}")

                obj = data.get("object") or {}
                items = obj.get("returnlist") or []
                if not items:
                    break

                for item in items:
                    publish_date = parse_date(item.get("receiveTime"))
                    if publish_date and publish_date < cutoff:
                        continue
                    title = clean_text(
                        item.get("businessObjectName") or item.get("tenderProjectName") or ""
                    )
                    if not title:
                        continue
                    business_id = clean_text(
                        item.get("businessId") or item.get("tenderProjectCode") or ""
                    )
                    if not business_id:
                        continue
                    deadline = parse_date(item.get("bulletinEndTime"))
                    if deadline and deadline.startswith("1970"):
                        deadline = None
                    business_keyword = {
                        "招标公告": "tenderBulletin",
                        "中标公告": "winBidBulletin",
                        "评标公示": "winCandidateBulletin",
                        "开标记录": "openBidRecord",
                        "资格预审公告": "tenderBulletin",
                    }.get(type_name, "tenderBulletin")
                    results.append(
                        Tender(
                            source=self.id,
                            external_id=business_id,
                            title=title,
                            publish_date=publish_date,
                            deadline=deadline,
                            region=clean_text(item.get("regionName")) or None,
                            category=clean_text(item.get("industriesType")) or type_name,
                            amount=parse_amount(item.get("budgetAmount"), title),
                            url=(
                                f"{DETAIL}?businessId={business_id}"
                                f"&businessKeyWord={business_keyword}"
                            ),
                            summary=clean_text(item.get("transactionPlatfName")) or None,
                            raw=item,
                        )
                    )
                    collected += 1
                    if collected >= per_type_limit:
                        break

                page_info = obj.get("page") or {}
                total = int(page_info.get("totalCount") or 0)
                if page * 50 >= total:
                    break
                page += 1

        # 去重后按发布时间倒序
        unique = {item.id: item for item in results}
        ordered = sorted(
            unique.values(),
            key=lambda item: item.publish_date or "",
            reverse=True,
        )
        return ordered[:limit]

    @staticmethod
    def _resolve_types(business_type: str) -> list[str]:
        if business_type in (None, "", "all", "全部"):
            return [BUSINESS_TYPES["tender"], BUSINESS_TYPES["award"]]
        if business_type in BUSINESS_TYPES:
            return [BUSINESS_TYPES[business_type]]
        return [business_type]

    def _reset_session(self) -> None:
        """平台按会话限制免验证次数，每次查询换一个干净会话并预热。"""
        try:
            self.client.close()
        except Exception:
            pass
        self.client = make_client(referer=PORTAL, timeout=30.0)
        try:
            self.client.get(PORTAL, timeout=15.0)
        except Exception:
            pass
