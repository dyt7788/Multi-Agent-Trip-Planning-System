"""高德地图MCP服务封装"""

import json
import re
from typing import List, Dict, Any, Optional
from hello_agents.tools import MCPTool
from ..config import get_settings
from ..models.schemas import Location, POIInfo, WeatherInfo

# 全局MCP工具实例
_amap_mcp_tool = None


def get_amap_mcp_tool() -> MCPTool:
    """
    获取高德地图MCP工具实例(单例模式)
    
    Returns:
        MCPTool实例
    """
    global _amap_mcp_tool
    
    if _amap_mcp_tool is None:
        settings = get_settings()
        
        if not settings.amap_api_key:
            raise ValueError("高德地图API Key未配置,请在.env文件中设置AMAP_API_KEY")
        
        # 创建MCP工具
        _amap_mcp_tool = MCPTool(
            name="amap",
            description="高德地图服务,支持POI搜索、路线规划、天气查询等功能",
            server_command=["uvx", "amap-mcp-server"],
            env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
            auto_expand=True  # 自动展开为独立工具
        )
        
        print(f"✅ 高德地图MCP工具初始化成功")
        print(f"   工具数量: {len(_amap_mcp_tool._available_tools)}")
        
        # 打印可用工具列表
        if _amap_mcp_tool._available_tools:
            print("   可用工具:")
            for tool in _amap_mcp_tool._available_tools[:5]:  # 只打印前5个
                print(f"     - {tool.get('name', 'unknown')}")
            if len(_amap_mcp_tool._available_tools) > 5:
                print(f"     ... 还有 {len(_amap_mcp_tool._available_tools) - 5} 个工具")
    
    return _amap_mcp_tool


class AmapService:
    """高德地图服务封装类"""
    
    def __init__(self):
        """初始化服务"""
        self.mcp_tool = get_amap_mcp_tool()

    @staticmethod
    def _extract_payload(raw_result: Any) -> Dict[str, Any]:
        """MCPTool may return dict or prefixed string; normalize to dict payload."""
        if isinstance(raw_result, dict):
            return raw_result
        if not isinstance(raw_result, str):
            return {}

        text = raw_result.strip()
        if not text:
            return {}

        # Fast path: raw result itself is a JSON object string
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                pass

        match = re.search(r"\{[\s\S]*\}", raw_result)
        if not match:
            return {}

        try:
            return json.loads(match.group(0))
        except Exception:
            return {}

    @classmethod
    def _extract_json_from_text(cls, text: str) -> Dict[str, Any]:
        if not isinstance(text, str):
            return {}
        return cls._extract_payload(text)

    @classmethod
    def _extract_data_from_payload(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize different MCP return formats into a single data dict."""
        if not isinstance(payload, dict) or not payload:
            return {}

        # Format A: payload is already the data body (common for amap-mcp-server)
        direct_keys = {
            "pois", "suggestion", "forecasts", "lives", "route", "geocodes", "regeocode",
            "results", "tips", "distance", "paths", "status", "info",
        }
        detail_keys = {
            "id", "name", "location", "address", "city", "type", "business_area",
            "rating", "cost", "open_time", "opentime2",
        }
        if any(k in payload for k in direct_keys):
            return payload
        # maps_search_detail often returns a single POI detail object directly.
        if len([k for k in detail_keys if k in payload]) >= 2:
            return payload

        # Format B: payload["return"] wraps result entries
        returns = payload.get("return")
        if isinstance(returns, list):
            for item in returns:
                if isinstance(item, dict):
                    if any(k in item for k in direct_keys):
                        return item

                    content = item.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict):
                                if isinstance(block.get("json"), dict):
                                    return block["json"]
                                block_text = block.get("text")
                                parsed = cls._extract_json_from_text(block_text) if isinstance(block_text, str) else {}
                                if parsed:
                                    return parsed

                    item_text = item.get("text")
                    parsed_item = cls._extract_json_from_text(item_text) if isinstance(item_text, str) else {}
                    if parsed_item:
                        return parsed_item

        # Format C: payload text fields carry JSON
        payload_text = payload.get("text")
        parsed_payload_text = cls._extract_json_from_text(payload_text) if isinstance(payload_text, str) else {}
        if parsed_payload_text:
            return parsed_payload_text

        # Last-resort: if payload itself already looks like a detail record, keep it.
        if len([k for k in detail_keys if k in payload]) >= 2:
            return payload

        return {}

    def call_tool_json(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call MCP tool and return normalized structured response for generated scripts."""
        try:
            raw_result = self.mcp_tool.run({
                "action": "call_tool",
                "tool_name": tool_name,
                "arguments": arguments,
            })
            print("MCP工具调用原始结果:", raw_result if isinstance(raw_result, str) else raw_result)
        except Exception as e:
            return {
                "ok": False,
                "tool_name": tool_name,
                "data": {},
                "payload": {},
                "raw": None,
                "error": str(e),
            }

        payload = self._extract_payload(raw_result)
        data = self._extract_data_from_payload(payload)
        if isinstance(data, dict) and data:
            return {
                "ok": True,
                "tool_name": tool_name,
                "data": data,
                "payload": payload,
                "raw": raw_result,
            }

        return {
            "ok": False,
            "tool_name": tool_name,
            "data": {},
            "payload": payload if isinstance(payload, dict) else {},
            "raw": raw_result,
            "error": "MCP调用成功但未返回可解析的结构化数据(return为空或文本中JSON不可解析)",
        }
    
    def search_poi(self, keywords: str, city: str, citylimit: bool = True) -> List[POIInfo]:
        """
        搜索POI
        
        Args:
            keywords: 搜索关键词
            city: 城市
            citylimit: 是否限制在城市范围内
            
        Returns:
            POI信息列表
        """
        try:
            # 调用MCP工具
            result = self.mcp_tool.run({
                "action": "call_tool",
                "tool_name": "maps_text_search",
                "arguments": {
                    "keywords": keywords,
                    "city": city,
                    "citylimit": str(citylimit).lower()
                }
            })
            
            # 解析结果
            # 注意: MCP工具返回的是字符串,需要解析
            # 这里简化处理,实际应该解析JSON
            print(f"POI搜索结果: {result[:200]}...")  # 打印前200字符
            
            # TODO: 解析实际的POI数据
            return []
            
        except Exception as e:
            print(f"❌ POI搜索失败: {str(e)}")
            return []
    
    def get_weather(self, city: str) -> List[WeatherInfo]:
        """
        查询天气
        
        Args:
            city: 城市名称
            
        Returns:
            天气信息列表
        """
        try:
            # 调用MCP工具
            result = self.mcp_tool.run({
                "action": "call_tool",
                "tool_name": "maps_weather",
                "arguments": {
                    "city": city
                }
            })
            
            print(f"天气查询结果: {result[:200]}...")
            
            # TODO: 解析实际的天气数据
            return []
            
        except Exception as e:
            print(f"❌ 天气查询失败: {str(e)}")
            return []
    
    def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking"
    ) -> Dict[str, Any]:
        """
        规划路线
        
        Args:
            origin_address: 起点地址
            destination_address: 终点地址
            origin_city: 起点城市
            destination_city: 终点城市
            route_type: 路线类型 (walking/driving/transit)
            
        Returns:
            路线信息
        """
        try:
            # 根据路线类型选择工具
            tool_map = {
                "walking": "maps_direction_walking_by_address",
                "driving": "maps_direction_driving_by_address",
                "transit": "maps_direction_transit_integrated_by_address"
            }
            
            tool_name = tool_map.get(route_type, "maps_direction_walking_by_address")
            
            # 构建参数
            arguments = {
                "origin_address": origin_address,
                "destination_address": destination_address
            }
            
            # 公共交通需要城市参数
            if route_type == "transit":
                if origin_city:
                    arguments["origin_city"] = origin_city
                if destination_city:
                    arguments["destination_city"] = destination_city
            else:
                # 其他路线类型也可以提供城市参数提高准确性
                if origin_city:
                    arguments["origin_city"] = origin_city
                if destination_city:
                    arguments["destination_city"] = destination_city
            
            # 调用MCP工具
            result = self.mcp_tool.run({
                "action": "call_tool",
                "tool_name": tool_name,
                "arguments": arguments
            })
            
            print(f"路线规划结果: {result[:200]}...")
            
            # TODO: 解析实际的路线数据
            return {}
            
        except Exception as e:
            print(f"❌ 路线规划失败: {str(e)}")
            return {}
    
    def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        """
        地理编码(地址转坐标)

        Args:
            address: 地址
            city: 城市

        Returns:
            经纬度坐标
        """
        try:
            arguments = {"address": address}
            if city:
                arguments["city"] = city

            result = self.mcp_tool.run({
                "action": "call_tool",
                "tool_name": "maps_geo",
                "arguments": arguments
            })

            print(f"地理编码结果: {result[:200]}...")

            # TODO: 解析实际的坐标数据
            return None

        except Exception as e:
            print(f"❌ 地理编码失败: {str(e)}")
            return None

    def get_poi_detail(self, poi_id: str) -> Dict[str, Any]:
        """
        获取POI详情

        Args:
            poi_id: POI ID

        Returns:
            POI详情信息
        """
        try:
            result = self.mcp_tool.run({
                "action": "call_tool",
                "tool_name": "maps_search_detail",
                "arguments": {
                    "id": poi_id
                }
            })

            print(f"POI详情结果: {result[:200]}...")

            # 解析结果并提取图片
            import json
            import re

            # 尝试从结果中提取JSON
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return data

            return {"raw": result}

        except Exception as e:
            print(f"❌ 获取POI详情失败: {str(e)}")
            return {}


# 创建全局服务实例
_amap_service = None


def get_amap_service() -> AmapService:
    """获取高德地图服务实例(单例模式)"""
    global _amap_service
    
    if _amap_service is None:
        _amap_service = AmapService()
    
    return _amap_service

# ============ 测试 Demo ============

# ============ 修改后的测试 Demo ============

if __name__ == "__main__":
    import json
    
    # 1. 初始化服务
    service = get_amap_service()
    
    # print("\n" + "="*60)
    # print("🚀 开始：从地址到周边酒店的完整链路测试")
    # print("="*60)

    # # 目标地址
    # target_address = "赵重公路135号"
    # target_city = "上海"

    # # --- 步骤 1：通过地址获取 Location (地理编码) ---
    # # --- 步骤 1：通过地址获取 Location (地理编码) ---
    # print(f"\n🔍 步骤 1: 正在解析地址 [{target_address}] 的坐标...")
    
    # geo_result = service.call_tool_json(
    #     tool_name="maps_geo",
    #     arguments={
    #         "address": target_address,
    #         "city": target_city
    #     }
    # )

    # location = None
    # # 兼容性处理：如果 ok 为 False 但 raw 中其实有数据
    # raw_data = geo_result.get("raw", "")
    # data = geo_result.get("data", {})

    # # 尝试多种路径获取坐标
    # if isinstance(data, dict) and data.get("geocodes"):
    #     # 路径 A: 标准字典格式
    #     location = data["geocodes"][0].get("location")
    # elif isinstance(data, list) and len(data) > 0:
    #     # 路径 B: 直接返回了列表 (对应你截图的情况)
    #     print("⚠️ 注意: MCP返回了一个列表而非字典,尝试从列表中提取坐标")
    #     location = data[0].get("location")
    # elif "location" in str(raw_data):
    #     # 路径 C: 暴力提取 (兜底方案)
    #     print("⚠️ 注意: MCP返回的文本中包含坐标,尝试使用正则表达式提取")
    #     import re
    #     match = re.search(r'"location":\s*"([\d\.,]+)"', str(raw_data))
    #     if match:
    #         location = match.group(1)

    # if location:
    #     print(f"✅ 成功解析坐标: {location}")
    # else:
    #     print(f"❌ 解析失败。原始输出片段: {str(raw_data)[:100]}")

    # # --- 步骤 2：根据解析出的 Location 搜索周边酒店 ---
    # if location:
    #     print(f"\n🏨 步骤 2: 正在搜索坐标 [{location}] 周边的酒店...")
        
    #     hotel_result = service.call_tool_json(
    #         tool_name="maps_around_search",
    #         arguments={
    #             "location": location,
    #             "keywords": "酒店",
    #             "radius": "2000",        # 注意：这里必须是字符串
    #             "sortrule": "distance",
    #             "extensions": "all"      # 确保返回价格和评分
    #         }
    #     )

    #     if hotel_result.get("ok"):
    #         pois = hotel_result.get("data", {}).get("pois", [])
    #         print(f"✅ 成功找到 {len(pois)} 家周边酒店\n")
            
    #         for i, poi in enumerate(pois[:3]):
    #             biz_ext = poi.get("biz_ext", {})
    #             print(f"[{i+1}] {poi.get('name')}")
    #             print(f"    - 距离: {poi.get('distance')}米")
    #             print(f"    - 评分: {biz_ext.get('rating', 'N/A')}")
    #             print(f"    - 价格: {biz_ext.get('lowest_price', 'N/A')}元")
    #             print(f"    - 地址: {poi.get('address')}")
    #             print("-" * 40)
    #     else:
    #         print(f"❌ 酒店搜索失败: {hotel_result.get('error')}")
    # else:
    #     print("🛑 由于未获取到有效坐标，停止后续酒店搜索。")

    # 示例：查看海丰快捷酒店的详细信息
    detail_info = service.get_poi_detail("B00156R4GP")
    print(detail_info)