# ===== scripts/backup_data.sh =====
#!/bin/bash
# Backup script for database and logs

BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "📦 Creating backup in $BACKUP_DIR"

# Backup database
if [ -f "data/arbitrage.db" ]; then
    cp data/arbitrage.db "$BACKUP_DIR/"
    echo "✅ Database backed up"
fi

# Backup logs
if [ -d "logs" ]; then
    tar -czf "$BACKUP_DIR/logs.tar.gz" logs/
    echo "✅ Logs backed up"
fi

# Backup opportunities CSVs
if ls arbitrage_opportunities_*.csv 1> /dev/null 2>&1; then
    mkdir -p "$BACKUP_DIR/opportunities"
    cp arbitrage_opportunities_*.csv "$BACKUP_DIR/opportunities/"
    echo "✅ Opportunity CSVs backed up"
fi

echo "✅ Backup complete!"

# Keep only last 7 days of backups
find backups -type d -mtime +7 -exec rm -rf {} \; 2>/dev/null || true
echo "🧹 Old backups cleaned up"