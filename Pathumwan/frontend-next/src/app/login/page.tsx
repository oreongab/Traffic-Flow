"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { login } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { setAuth } = useAuth();
  const router = useRouter();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await login(identifier, password);
      setAuth(res.data.token, res.data.user);
      router.push("/dashboard");
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string; message?: string } } })
          ?.response?.data?.error ||
        (err as { response?: { data?: { error?: string; message?: string } } })
          ?.response?.data?.message ||
        "เข้าสู่ระบบไม่สำเร็จ";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen">
      {/* Left — Logo */}
      <div className="hidden md:flex w-1/2 bg-white items-center justify-center">
        <div className="text-center">
          <div className="text-8xl mb-4">🚦</div>
          <h1 className="text-4xl font-bold">
            <span className="text-[#4a90d9]">Traffix</span>
            <span className="text-[#4a90d9]">Flow</span>
          </h1>
        </div>
      </div>

      {/* Right — Form */}
      <div className="flex-1 bg-[#5ba8e0] flex items-center justify-center px-6">
        <div className="w-full max-w-md">
          <h2 className="text-3xl font-bold text-white text-center mb-2">
            ยินดีต้อนรับ
          </h2>
          <p className="text-white/80 text-center mb-8">
            เข้าสู่ประสบการณ์บนท้องถนน
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <p className="text-red-100 text-sm bg-red-500/30 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            <input
              type="text"
              required
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              className="w-full bg-white rounded-lg px-4 py-3 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-white/50"
              placeholder="ชื่อผู้ใช้งาน/อีเมล"
            />

            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-white rounded-lg px-4 py-3 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-white/50"
              placeholder="รหัสผ่าน"
            />

            <div className="text-right">
              <Link
                href="/reset-password"
                className="text-sm text-white/90 hover:text-white hover:underline"
              >
                ลืมรหัสผ่าน?
              </Link>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-[#1e3a5f] hover:bg-[#162e4a] disabled:opacity-50 text-white font-medium rounded-lg py-3 transition-colors text-lg"
            >
              {loading ? "กำลังเข้าสู่ระบบ..." : "เข้าสู่ระบบ"}
            </button>

            <p className="text-center text-sm text-white/90">
              <Link
                href="/register"
                className="text-white hover:underline font-medium"
              >
                สมัครบัญชีผู้ใช้
              </Link>
            </p>
          </form>
        </div>
      </div>
    </div>
  );
}
