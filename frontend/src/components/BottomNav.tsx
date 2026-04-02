import { Link, useLocation } from "react-router-dom";

const navItems = [
  { path: "/", label: "ホーム", icon: "🏠" },
  { path: "/history", label: "履歴", icon: "📊" },
  { path: "/settings", label: "設定", icon: "⚙️" },
];

export default function BottomNav() {
  const { pathname } = useLocation();

  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-[#16213e] border-t border-gray-700">
      <div className="max-w-md md:max-w-2xl lg:max-w-3xl mx-auto flex justify-around">
        {navItems.map((item) => (
          <Link
            key={item.path}
            to={item.path}
            className={`flex flex-col items-center py-2 px-4 text-xs ${
              pathname === item.path ? "text-rose-400" : "text-gray-400"
            }`}
          >
            <span className="text-lg">{item.icon}</span>
            <span>{item.label}</span>
          </Link>
        ))}
      </div>
    </nav>
  );
}
