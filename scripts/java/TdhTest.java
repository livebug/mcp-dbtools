import java.sql.*;

public class TdhTest {
    public static void main(String[] args) throws Exception {
        String url = args.length > 0 ? args[0] : "jdbc:hive2://127.0.0.1:10208/default";
        String user = args.length > 1 ? args[1] : "root";
        String pass = args.length > 2 ? args[2] : "";
        String sql = args.length > 3 ? args[3] : "show databases";
        try {
            Class.forName("org.apache.hive.jdbc.HiveDriver");
            System.out.println("URL: " + url);
            try (Connection c = DriverManager.getConnection(url, user, pass)) {
                System.out.println("CONNECT OK, product=" + c.getMetaData().getDatabaseProductName()
                        + " version=" + c.getMetaData().getDatabaseProductVersion());
                try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery(sql)) {
                    ResultSetMetaData md = rs.getMetaData();
                    int n = md.getColumnCount();
                    while (rs.next()) {
                        StringBuilder sb = new StringBuilder();
                        for (int i = 1; i <= n; i++) {
                            if (i > 1) sb.append(" | ");
                            sb.append(rs.getString(i));
                        }
                        System.out.println("ROW: " + sb);
                    }
                }
            }
        } catch (Exception e) {
            System.out.println("FAIL: " + e);
            e.printStackTrace();
        }
    }
}
